import base64
import json
import os
import ssl
import threading
import time
import uuid
from abc import ABC, abstractmethod
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import httpx
from openai import OpenAI


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt, *, max_tokens=8192):
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, *, api_key, base_url, model, ca_bundle=None):
        if not api_key:
            raise RuntimeError("Для выбранного провайдера не задан API-ключ в .env.")
        if not model:
            raise RuntimeError("Для выбранного провайдера не задана модель в .env.")
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.ca_bundle = ca_bundle

    def complete(self, prompt, *, max_tokens=8192):
        http_client = httpx.Client(
            verify=self.ca_bundle or True,
            timeout=240,
        )
        try:
            with OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=240,
                max_retries=1,
                http_client=http_client,
            ) as client:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.7,
                )
        except Exception as error:
            raise RuntimeError(f"Ошибка LLM-провайдера: {error}") from error
        text = response.choices[0].message.content
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("LLM-провайдер вернул пустой ответ.")
        return text.strip()


class GigaChatProvider(OpenAICompatibleProvider):
    TOKEN_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    BASE_URL = "https://api.giga.chat/v1"
    _token = None
    _token_expires_at = 0
    _token_lock = threading.Lock()

    def __init__(self, authorization_key, scope, model):
        ca_bundle = os.getenv("GIGACHAT_CA_BUNDLE", "").strip() or None
        if ca_bundle and not os.path.isfile(ca_bundle):
            raise RuntimeError("Файл GIGACHAT_CA_BUNDLE не найден.")
        super().__init__(
            api_key="pending",
            base_url=self.BASE_URL,
            model=model,
            ca_bundle=ca_bundle,
        )
        self.authorization_key = authorization_key.removeprefix("Basic ").strip()
        self.scope = scope
        if not self.authorization_key:
            raise RuntimeError("Задайте GIGACHAT_API_KEY — ключ авторизации из личного кабинета GigaChat API.")

    def _get_access_token(self):
        now = time.time()
        with self._token_lock:
            if self._token and now < self._token_expires_at - 60:
                return self._token

            body = urlencode({"scope": self.scope}).encode("ascii")
            request = Request(
                self.TOKEN_URL,
                data=body,
                headers={
                    "Authorization": f"Basic {self.authorization_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": str(uuid.uuid4()),
                },
                method="POST",
            )
            try:
                context = ssl.create_default_context(cafile=self.ca_bundle)
                with urlopen(request, timeout=30, context=context) as response:
                    token_data = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                details = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GigaChat не выдал access token (HTTP {error.code}): {details[:400]}"
                ) from error
            except (URLError, TimeoutError) as error:
                raise RuntimeError(f"Не удалось подключиться к GigaChat: {error}") from error

            token = token_data.get("access_token")
            if not token:
                raise RuntimeError("Ответ GigaChat не содержит access_token.")
            expires_at = float(token_data.get("expires_at", now + 1800))
            if expires_at > 1_000_000_000_000:
                expires_at /= 1000
            self.__class__._token = token
            self.__class__._token_expires_at = expires_at
            return token

    def generate(self, prompt, *, max_tokens=8192):
        self.api_key = self._get_access_token()
        return self.complete(prompt, max_tokens=max_tokens)


class OpenRouterProvider(OpenAICompatibleProvider):
    BASE_URL = "https://openrouter.ai/api/v1"

    def generate(self, prompt, *, max_tokens=8192):
        return self.complete(prompt, max_tokens=max_tokens)


def create_provider(provider_name=None):
    provider_name = (provider_name or os.getenv("LLM_PROVIDER", "GIGACHAT")).strip().upper()
    if provider_name == "GIGACHAT":
        return GigaChatProvider(
            authorization_key=os.getenv("GIGACHAT_API_KEY", ""),
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            model=os.getenv("GIGACHAT_MODEL", "GigaChat-3-Ultra"),
        )
    if provider_name == "OPENROUTER":
        return OpenRouterProvider(
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            base_url=OpenRouterProvider.BASE_URL,
            model=os.getenv("OPENROUTER_MODEL", ""),
        )
    raise RuntimeError("Неизвестный LLM_PROVIDER. Выберите GIGACHAT или OPENROUTER.")
