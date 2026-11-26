import json
import os
import re
import time
from collections import defaultdict, deque
from urllib.error import HTTPError, URLError
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from llm import create_provider
from pdf_export import make_book_pdf


ROOT = Path(__file__).resolve().parent
STATIC_FILES = {"index.html", "app.js", "styles.css", "favicon.ico"}
RATE_LIMITS = {"generate": (10, 3600), "pdf": (10, 3600)}
request_history = defaultdict(lambda: deque())


def client_ip(handler):
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return handler.client_address[0]


def is_rate_limited(handler, route):
    limit, window = RATE_LIMITS[route]
    now = time.time()
    history = request_history[(client_ip(handler), route)]
    while history and now - history[0] > window:
        history.popleft()
    if len(history) >= limit:
        return True
    history.append(now)
    return False


def load_local_env():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def prompt_for(payload):
    kind = payload.get("kind")
    book = payload.get("book") or {}
    title = str(book.get("title", "Новая книга"))[:100]
    idea = str(book.get("idea", ""))[:5000]
    details = str(book.get("details", ""))[:3000]
    genre = str(book.get("genre", ""))[:80]
    language = "English" if book.get("language") == "en" else "Russian"
    count = max(2, min(int(book.get("chapterCount", 6)), 16))

    if kind == "outline":
        return (
            "You are a careful book-development editor. Create an original, coherent chapter outline. "
            "Use the user's broad genre and premise, not the style of any named living author. "
            f"Write all values in {language}. Return ONLY valid JSON, no markdown, with this schema: "
            '{"chapters":[{"title":"short chapter title","summary":"2-4 sentence concrete plot summary"}]}. '
            f"Create exactly {count} chapters. Book title: {title}. Genre: {genre}. "
            f"Idea: {idea}. Character and story notes: {details}. "
            "Make each chapter causally advance the story, vary its function, maintain character continuity, "
            "and give the final chapter a satisfying resolution. "
            "Give each chapter a concrete, different event: avoid repeating a meeting, discovery, or chase. "
            "Track every character by name and number: never change a team size or imply that a listed "
            "character is present when they are absent. Do not add unnamed important companions."
        )

    if kind == "chapter":
        chapter = payload.get("chapter") or {}
        chapter_title = str(chapter.get("title", ""))[:120]
        summary = str(chapter.get("summary", ""))[:1200]
        note = str(chapter.get("note", ""))[:1200]
        prior = payload.get("previousSummaries", [])
        if not isinstance(prior, list):
            prior = []
        prior_text = "\n".join(str(item)[:500] for item in prior[:8])
        target = max(500, min(int(book.get("targetWords", 20000)) // count, 3500))
        return (
            "Write one original literary-fiction chapter in response to the approved outline. "
            "Follow the premise and chapter brief. Do not imitate any named author or reproduce existing works. "
            f"Write in {language}. Aim for about {target} words, but prioritize a complete coherent chapter. "
            "Before writing, silently build a continuity checklist from the premise and earlier summaries: "
            "character names, exact roster, locations, chronology, injuries, possessions, and prior decisions. "
            "Keep names and ages consistent. Do not introduce a major character who was not established unless "
            "the chapter plan explicitly requires it. Do not contradict the exact number or roles of the team. "
            "Use natural literary Russian: avoid English calques, technical-sounding translationese, and "
            "generic corporate words unless they are organically part of the setting. "
            "Avoid repeating plot exposition from earlier summaries; vary scene length and action; allow "
            "characters to disagree or make meaningful choices. "
            "Make physical details plausible and verify practical objects, transportation, weapons, and "
            "state institutions in the story. Do not add sensory claims that contradict established traits "
            "(for example, a known blind character perceiving an ordinary scene as sighted). "
            "Return only the chapter prose, without a title or commentary. "
            f"Book title: {title}. Genre: {genre}. Premise: {idea}. Character and world notes: {details}. "
            f"Earlier chapter summaries:\n{prior_text}\n"
            f"Current chapter title: {chapter_title}. Chapter plan: {summary}. Author note: {note}."
        )
    raise ValueError("Неизвестный тип генерации.")


def parse_outline(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise RuntimeError("Модель вернула план в неверном формате. Попробуйте ещё раз.")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as error:
            raise RuntimeError("Не удалось разобрать план от модели. Попробуйте ещё раз.") from error
    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        raise RuntimeError("Модель вернула план без списка глав.")
    normalized = []
    for chapter in chapters:
        if isinstance(chapter, dict) and str(chapter.get("title", "")).strip():
            normalized.append({
                "title": str(chapter["title"]).strip()[:120],
                "summary": str(chapter.get("summary", "")).strip()[:1200],
            })
    if len(normalized) < 2:
        raise RuntimeError("В плане должно быть не менее двух глав.")
    return normalized[:16]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, max_size=64_000):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_size:
            raise ValueError(f"Запрос пустой или слишком большой (максимум {max_size // 1000} КБ).")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Ожидался JSON-объект.")
        return payload

    def require_signed_in_user(self):
        supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()
        if not supabase_url and not anon_key:
            self.send_json(503, {"error": "Для генерации и экспорта необходима настройка Supabase Auth."})
            return False
        if not supabase_url or not anon_key:
            self.send_json(503, {"error": "Supabase настроен не полностью: задайте URL и anon key."})
            return False
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            self.send_json(401, {"error": "Войдите в аккаунт Supabase, чтобы продолжить."})
            return False
        request = Request(
            f"{supabase_url}/auth/v1/user",
            headers={
                "apikey": anon_key,
                "Authorization": authorization,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                user = json.loads(response.read().decode("utf-8"))
            if not isinstance(user, dict) or not user.get("id"):
                self.send_json(401, {"error": "Сессия Supabase недействительна. Войдите снова."})
                return False
            return True
        except HTTPError as error:
            self.send_json(401, {"error": "Сессия Supabase истекла. Войдите снова."})
            return False
        except (URLError, TimeoutError) as error:
            self.send_json(502, {"error": f"Не удалось проверить вход в Supabase: {error}"})
            return False

    def send_provider_error(self):
        provider = os.getenv("LLM_PROVIDER", "GIGACHAT").strip().upper()
        try:
            create_provider(provider)
            configured = True
            message = None
        except RuntimeError as error:
            configured = False
            message = str(error)
        return provider, configured, message

    def do_GET(self):
        if self.path == "/api/config":
            provider, configured, error = self.send_provider_error()
            self.send_json(200, {
                "provider": provider,
                "providerConfigured": configured,
                "providerError": error,
                "supabaseUrl": os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
                "supabaseAnonKey": os.getenv("SUPABASE_ANON_KEY", "").strip(),
            })
            return
        if self.path == "/api/status":
            provider, configured, error = self.send_provider_error()
            self.send_json(200, {"configured": configured, "provider": provider, "error": error})
            return
        if self.path in ("", "/"):
            self.path = "/index.html"
        else:
            self.path = self.path.split("?", 1)[0]
        if self.path.lstrip("/") not in STATIC_FILES:
            self.send_error(404)
            return
        super().do_GET()

    def do_POST(self):
        if self.path not in ("/api/generate", "/api/export/pdf"):
            self.send_json(404, {"error": "Маршрут не найден."})
            return
        if not self.require_signed_in_user():
            return
        route = "pdf" if self.path == "/api/export/pdf" else "generate"
        if is_rate_limited(self, route):
            self.send_json(429, {"error": "Превышен лимит запросов. Повторите попытку позже."})
            return
        try:
            payload = self.read_json(1_000_000 if self.path == "/api/export/pdf" else 64_000)
            if self.path == "/api/export/pdf":
                book = payload.get("book")
                if not isinstance(book, dict):
                    raise ValueError("В запросе нет книги.")
                pdf = make_book_pdf(book)
                filename = quote(
                    re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(book.get("title") or "book"))[:80] + ".pdf"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
                self.send_header("Content-Length", str(len(pdf)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(pdf)
            else:
                prompt = prompt_for(payload)
                provider = create_provider()
                generated = provider.generate(prompt)
                if payload.get("kind") == "outline":
                    self.send_json(200, {"chapters": parse_outline(generated)})
                else:
                    self.send_json(200, {"text": generated})
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error) or "Некорректный запрос."})
        except RuntimeError as error:
            self.send_json(502, {"error": str(error)})
        except Exception as error:
            self.log_error("Unexpected generation error: %s", error)
            self.send_json(500, {"error": "Внутренняя ошибка генерации. Подробности смотрите в окне сервера."})


def main():
    load_local_env()
    host = os.environ.get("HOST", os.environ.get("LITERA_HOST", "127.0.0.1"))
    port = int(os.environ.get("PORT", os.environ.get("LITERA_PORT", "8000")))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Литера запущена: http://{host}:{port}")
    print("Остановить сервер: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
