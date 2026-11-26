const STORAGE_KEY = "litera.books.v1";
const $ = (selector) => document.querySelector(selector);
const views = ["auth", "library", "create", "book", "plan", "editor"];
let books = loadBooks();
let activeBookId = null;
let activeChapterId = null;
let toastTimer;
let cloudClient = null;
let cloudSession = null;
let cloudUserId = null;
let cloudEnabled = false;
let storageKey = STORAGE_KEY;
let cloudSaveTimer;

function loadBooks() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(books));
  } catch {
    showToast("Не удалось сохранить. Возможно, в браузере закончилось место.", true);
    return false;
  }
  if (cloudClient && cloudSession && cloudUserId) {
    const book = getActiveBook();
    if (book) {
      clearTimeout(cloudSaveTimer);
      cloudSaveTimer = setTimeout(() => {
        void cloudClient.from("books").upsert({
          id: book.id,
          owner: cloudUserId,
          book,
          updated_at: new Date().toISOString()
        }).then(({ error }) => {
          if (error) showToast(`Не удалось сохранить в Supabase: ${error.message}`, true);
        }).catch((error) => {
          showToast(`Не удалось сохранить в Supabase: ${error.message || error}`, true);
        });
      }, 700);
    }
  }
  return true;
}

function uid() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3200);
}

function setView(name, breadcrumb) {
  if (cloudEnabled && !cloudSession && name !== "auth") name = "auth";
  for (const view of views) $(`#${view}-view`).classList.toggle("active", view === name);
  $("#breadcrumb-current").textContent = breadcrumb || ({
    auth: "Вход",
    library: "Мои книги", create: "Новая книга", book: getActiveBook()?.title || "Книга",
    plan: "План истории", editor: "Редактор главы"
  })[name];
  $("#nav-library").classList.toggle("active", name === "library" || name === "book");
  $("#nav-create").classList.toggle("active", name === "create");
  $("#top-create").hidden = name === "auth";
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function getActiveBook() {
  return books.find((book) => book.id === activeBookId);
}

function renderLibrary() {
  const grid = $("#book-grid");
  grid.innerHTML = "";
  $("#project-count").textContent = `${books.length} ${books.length === 1 ? "книга" : books.length >= 2 && books.length <= 4 ? "книги" : "книг"}`;
  $("#empty-state").classList.toggle("hidden", books.length > 0);
  for (const book of [...books].sort((a, b) => b.updatedAt - a.updatedAt)) {
    const words = book.chapters.reduce((sum, chapter) => sum + wordCount(chapter.text || ""), 0);
    const completed = book.chapters.filter((chapter) => chapter.text?.trim()).length;
    const card = document.createElement("button");
    card.type = "button";
    const genreClass = {
      "Романтика": "genre-romance",
      "Фэнтези": "genre-fantasy",
      "Мистика и хоррор": "genre-horror",
      "Детектив": "genre-detective",
      "Приключения": "genre-adventure",
      "Драма": "genre-drama"
    }[book.genre] || "genre-other";
    card.className = `book-card ${genreClass}`;
    card.innerHTML = `<span class="card-genre">${escapeHtml(book.genre)} · ${book.language === "en" ? "English" : "Русский"}</span><h3>${escapeHtml(book.title)}</h3><span class="card-meta">${completed} из ${book.chapters.length} глав · ${words.toLocaleString("ru-RU")} слов</span>`;
    card.addEventListener("click", () => openBook(book.id));
    grid.append(card);
  }
}

function wordCount(text) {
  return (text.trim().match(/\S+/g) || []).length;
}

function makeOutline(book) {
  const names = book.language === "en"
    ? ["A chance encounter", "The first complication", "A secret comes to light", "An unexpected choice", "The turning point", "A new beginning"]
    : ["Случайная встреча", "Первое осложнение", "Тайна становится явной", "Неожиданный выбор", "Переломный момент", "Новое начало"];
  const total = book.chapterCount;
  return Array.from({ length: total }, (_, index) => {
    const progress = (index + 1) / total;
    const role = index === 0 ? (book.language === "en" ? "Introduce the protagonists and the situation." : "Знакомство с героями и исходной ситуацией.")
      : index === total - 1 ? (book.language === "en" ? "Resolve the central conflict and show what has changed." : "Разрешение главного конфликта и итог перемен.")
      : progress < .35 ? (book.language === "en" ? "Build the connection and introduce the first obstacle." : "Развитие отношений и первое препятствие.")
      : progress < .7 ? (book.language === "en" ? "Raise the stakes; let the characters make a consequential choice." : "Повышение ставок и важный выбор героев.")
      : (book.language === "en" ? "Bring the conflict to its climax and prepare the resolution." : "Кульминация конфликта и подготовка развязки.");
    return {
      id: uid(),
      title: names[Math.min(index, names.length - 1)] + (index >= names.length ? ` ${index + 1}` : ""),
      summary: `${role} ${book.idea.slice(0, 180)}${book.idea.length > 180 ? "…" : ""}`,
      text: "",
      note: ""
    };
  });
}

function createBook(form) {
  const data = new FormData(form);
  const idea = String(data.get("idea") || "").trim();
  if (!idea) return showToast("Опишите идею книги, чтобы продолжить.", true);
  const chapterCount = Number(data.get("chapters"));
  const targetWords = Number(data.get("words"));
  if (targetWords / chapterCount > 3500) {
    return showToast("Для такого объёма добавьте главы: одна глава ограничена примерно 3 500 словами.", true);
  }
  const title = String(data.get("title") || "").trim() || (idea.split(/[.!?\n]/)[0].slice(0, 45) || "Новая история");
  const book = {
    id: uid(),
    title,
    author: String(data.get("author") || "").trim() || "Автор",
    idea,
    details: String(data.get("details") || "").trim(),
    genre: String(data.get("genre")),
    language: String(data.get("language")),
    chapterCount,
    targetWords,
    chapters: [],
    planApproved: false,
    planEdited: false,
    createdAt: Date.now(),
    updatedAt: Date.now()
  };
  book.chapters = makeOutline(book);
  books.push(book);
  activeBookId = book.id;
  persist();
  renderPlan();
  setView("plan");
  $("#plan-source").textContent = "Черновой план-схема. Отредактируйте его или запросите план у ИИ.";
  void generateOutline(book);
}

async function generateOutline(book) {
  const button = $("#regenerate-plan");
  if (button) {
    button.disabled = true;
    button.textContent = "Составляем план…";
  }
  try {
    const result = await apiGenerate("outline", book);
    if (!Array.isArray(result.chapters) || result.chapters.length < 2) throw new Error("Сервис вернул некорректный план.");
    if (book.planEdited || book.planApproved) return;
    book.chapters = result.chapters.slice(0, 16).map((chapter) => ({
      id: uid(), title: String(chapter.title || "").slice(0, 120),
      summary: String(chapter.summary || "").slice(0, 1200), text: "", note: ""
    }));
    book.updatedAt = Date.now();
    persist();
    if (activeBookId === book.id && $("#plan-view").classList.contains("active")) {
      renderPlan();
      $("#plan-source").textContent = "План предложен ИИ — проверьте и отредактируйте перед утверждением.";
      showToast("План готов. Проверьте главы перед утверждением.");
    }
  } catch (error) {
    $("#plan-source").textContent = "Используется черновой план-схема — можно редактировать вручную.";
    if (!error.message.startsWith("ИИ-генератор не настроен.")) showToast(error.message, true);
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = "↻ Предложить новый план";
    }
  }
}

async function apiGenerate(kind, book, chapter) {
  const chapterIndex = chapter ? book.chapters.findIndex((item) => item.id === chapter.id) : -1;
  const previousSummaries = chapterIndex > 0
    ? book.chapters.slice(Math.max(0, chapterIndex - 8), chapterIndex).map((item) =>
      `${item.title}: ${(item.text || item.summary || "").slice(0, 500)}`)
    : [];
  const compactBook = {
    title: book.title,
    idea: book.idea,
    details: book.details,
    genre: book.genre,
    language: book.language,
    chapterCount: book.chapterCount,
    targetWords: book.targetWords
  };
  const headers = { "Content-Type": "application/json" };
  const accessToken = await getAccessToken();
  if (accessToken) headers.Authorization = "Bearer " + accessToken;
  const response = await fetch("/api/generate", {
    method: "POST",
    headers,
    body: JSON.stringify({
      kind,
      book: compactBook,
      chapter: chapter ? { title: chapter.title, summary: chapter.summary, note: chapter.note } : undefined,
      previousSummaries
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Ошибка генератора (${response.status}).`);
  return data;
}

async function getAccessToken() {
  if (!cloudClient) return null;
  const { data, error } = await cloudClient.auth.getSession();
  if (error) throw new Error(`Не удалось проверить сессию: ${error.message}`);
  cloudSession = data.session;
  if (!cloudSession) throw new Error("Сессия истекла — войдите в аккаунт снова.");
  return cloudSession.access_token;
}

function renderPlan() {
  const book = getActiveBook();
  if (!book) return;
  $("#plan-list").innerHTML = "";
  book.chapters.forEach((chapter, index) => {
    const item = document.createElement("div");
    item.className = "plan-item";
    item.innerHTML = `<div class="plan-number">${String(index + 1).padStart(2, "0")}</div><div><input maxlength="120" aria-label="Название главы ${index + 1}" value="${escapeHtml(chapter.title)}"><textarea maxlength="1200" aria-label="Описание главы ${index + 1}">${escapeHtml(chapter.summary)}</textarea></div>`;
    const [titleInput, summaryInput] = item.querySelectorAll("input, textarea");
    titleInput.addEventListener("input", () => { chapter.title = titleInput.value; book.planEdited = true; persist(); });
    summaryInput.addEventListener("input", () => { chapter.summary = summaryInput.value; book.planEdited = true; persist(); });
    $("#plan-list").append(item);
  });
}

function openBook(id) {
  activeBookId = id;
  renderBook();
  setView("book");
}

function renderBook() {
  const book = getActiveBook();
  if (!book) return;
  $("#book-heading").textContent = book.title;
  $("#book-heading").setAttribute("title", `Автор: ${book.author || "Автор"}`);
  $("#book-meta").textContent = `${book.genre.toUpperCase()} · ${book.language === "en" ? "ENGLISH" : "РУССКИЙ"} · ${book.planApproved ? "ПЛАН УТВЕРЖДЁН" : "ЧЕРНОВИК ПЛАНА"}`;
  $("#book-idea-preview").textContent = book.idea;
  const done = book.chapters.filter((chapter) => chapter.text?.trim()).length;
  const words = book.chapters.reduce((sum, chapter) => sum + wordCount(chapter.text || ""), 0);
  $("#book-progress-label").textContent = `${done} из ${book.chapters.length} глав готовы`;
  $("#book-word-count").textContent = `${words.toLocaleString("ru-RU")} слов`;
  $("#book-progress-bar").style.width = `${book.chapters.length ? done / book.chapters.length * 100 : 0}%`;
  $("#chapter-list").innerHTML = "";
  book.chapters.forEach((chapter, index) => {
    const row = document.createElement("div");
    row.className = "chapter-row";
    row.innerHTML = `<div><h3>${String(index + 1).padStart(2, "0")}. ${escapeHtml(chapter.title || "Без названия")}</h3><p>${escapeHtml(chapter.summary || "Добавьте краткое содержание в плане.")}</p></div><div><span class="chapter-status ${chapter.text?.trim() ? "done" : ""}">${chapter.text?.trim() ? `${wordCount(chapter.text)} слов` : "Не написана"}</span><span class="chapter-arrow">→</span></div>`;
    row.addEventListener("click", () => openEditor(chapter.id));
    $("#chapter-list").append(row);
  });
}

function openEditor(chapterId) {
  activeChapterId = chapterId;
  const book = getActiveBook();
  const chapter = book?.chapters.find((item) => item.id === chapterId);
  if (!chapter) return;
  $("#editor-chapter-label").textContent = `ГЛАВА ${book.chapters.indexOf(chapter) + 1} ИЗ ${book.chapters.length}`;
  $("#editor-title").value = chapter.title;
  $("#chapter-text").value = chapter.text;
  $("#chapter-note").value = chapter.note || "";
  $("#editor-summary").textContent = chapter.summary || "Пока нет описания главы.";
  updateEditorCount();
  setView("editor");
}

function saveChapter(showMessage = true) {
  const book = getActiveBook();
  const chapter = book?.chapters.find((item) => item.id === activeChapterId);
  if (!chapter) return;
  chapter.title = $("#editor-title").value.trim() || "Без названия";
  chapter.text = $("#chapter-text").value;
  chapter.note = $("#chapter-note").value;
  book.updatedAt = Date.now();
  persist();
  updateEditorCount();
  $("#save-status").textContent = `Сохранено в ${new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`;
  if (showMessage) showToast("Глава сохранена.");
}

function updateEditorCount() {
  $("#editor-word-count").textContent = `${wordCount($("#chapter-text").value).toLocaleString("ru-RU")} слов`;
}

async function generateAllChapters() {
  const book = getActiveBook();
  if (!book) return;
  const unwritten = book.chapters.filter((chapter) => !chapter.text?.trim());
  if (!unwritten.length) {
    showToast("Во всех главах уже есть текст.");
    return;
  }
  if (!window.confirm(`Написать ${unwritten.length} незаполненных глав подряд? Пожалуйста, оставьте страницу открытой.`)) return;

  const button = $("#generate-all");
  const status = $("#book-generation-status");
  button.disabled = true;
  status.hidden = false;
  try {
    for (let index = 0; index < unwritten.length; index += 1) {
      const chapter = unwritten[index];
      $("#book-generation-status-text").textContent =
        `Пишем главу ${index + 1} из ${unwritten.length}: ${chapter.title}`;
      const result = await apiGenerate("chapter", book, chapter);
      if (!result.text?.trim()) throw new Error(`Генератор вернул пустой текст для главы «${chapter.title}».`);
      chapter.text = result.text.trim();
      chapter.updatedAt = Date.now();
      persist();
      renderBook();
    }
    $("#book-generation-status-text").textContent = "Все незаполненные главы готовы.";
    showToast("Все незаполненные главы готовы.");
  } catch (error) {
    $("#book-generation-status-text").textContent = "Генерация остановлена.";
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    setTimeout(() => { status.hidden = true; }, 3000);
  }
}

async function writeChapter() {
  saveChapter(false);
  const book = getActiveBook();
  const chapter = book?.chapters.find((item) => item.id === activeChapterId);
  if (!book || !chapter) return;
  const button = $("#regenerate-chapter");
  if (chapter.text.trim() && !window.confirm("Заменить текущий текст главы новой версией? Сначала сохраните копию, если она нужна.")) return;
  button.disabled = true;
  button.textContent = "Пишем главу…";
  $("#chapter-text").setAttribute("aria-busy", "true");
  $("#generation-status").hidden = false;
  $("#generation-status-text").textContent = "ИИ пишет главу и проверяет сюжетные детали…";
  try {
    const result = await apiGenerate("chapter", book, chapter);
    if (!result.text?.trim()) throw new Error("Генератор вернул пустой текст.");
    $("#chapter-text").value = result.text.trim();
    saveChapter(false);
    $("#generation-status-text").textContent = "Глава готова и сохранена.";
    showToast("Новая версия главы готова и сохранена.");
  } catch (error) {
    $("#generation-status-text").textContent = "Не удалось создать главу.";
    showToast(error.message, true);
  } finally {
    $("#chapter-text").setAttribute("aria-busy", "false");
    button.disabled = false;
    button.innerHTML = "↻ Перегенерировать";
    setTimeout(() => { $("#generation-status").hidden = true; }, 2200);
  }
}

function downloadText() {
  const book = getActiveBook();
  if (!book) return;
  const contents = book.chapters.map((chapter, index) => `Глава ${index + 1}. ${chapter.title}\n\n${chapter.text || "[Глава ещё не написана]"}`).join("\n\n");
  downloadBlob(`${book.title}\n\n${book.idea}\n\n${contents}`, `${safeFilename(book.title)}.txt`, "text/plain;charset=utf-8");
}

function safeFilename(name) {
  return name.replace(/[<>:"/\\|?*\x00-\x1F]/g, "-").trim().slice(0, 80) || "book";
}

function downloadBlob(content, filename, type) {
  const blob = new Blob(["\ufeff", content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function downloadPdf() {
  const book = getActiveBook();
  if (!book) return;
  if (!book.chapters.some((chapter) => chapter.text?.trim())) {
    showToast("Сначала напишите хотя бы одну главу.", true);
    return;
  }
  const button = $("#download-book");
  button.disabled = true;
  button.textContent = "Создаём PDF…";
  try {
    const headers = { "Content-Type": "application/json" };
    const accessToken = await getAccessToken();
    if (accessToken) headers.Authorization = "Bearer " + accessToken;
    const response = await fetch("/api/export/pdf", {
      method: "POST",
      headers,
      body: JSON.stringify({ book })
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || `Не удалось создать PDF (${response.status}).`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${safeFilename(book.title)}.pdf`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("PDF готов и скачивается.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Скачать PDF";
  }
}

async function signIn(email, password, createAccount = false) {
  if (!cloudClient) return showToast("Supabase не настроен для этого сайта.", true);
  const method = createAccount ? "signUp" : "signInWithPassword";
  const { data, error } = await cloudClient.auth[method]({ email, password });
  if (error) return showToast(error.message, true);
  if (createAccount && !data.session) {
    $("#auth-note").textContent = "Аккаунт создан. Подтвердите адрес по письму, затем войдите.";
  } else {
    showToast(createAccount ? "Аккаунт создан." : "Вы вошли.");
  }
}

async function handleCloudSession(session) {
  cloudSession = session;
  const userId = session?.user?.id || null;
  if (!userId) {
    clearTimeout(cloudSaveTimer);
    cloudUserId = null;
    activeBookId = null;
    activeChapterId = null;
    books = [];
    storageKey = STORAGE_KEY;
    $("#sign-out").hidden = true;
    $("#storage-status").textContent = cloudEnabled ? "Войдите для доступа к библиотеке." : "Книги хранятся в этом браузере.";
    renderLibrary();
    if (cloudEnabled) setView("auth", "Вход");
    else setView("library");
    return;
  }
  if (cloudUserId === userId && $("#auth-view").classList.contains("active") === false) return;
  if (cloudUserId !== userId) {
    clearTimeout(cloudSaveTimer);
    activeBookId = null;
    activeChapterId = null;
  }
  cloudUserId = userId;
  storageKey = `${STORAGE_KEY}.${userId}`;
  $("#sign-out").hidden = false;
  $("#storage-status").textContent = "Загружаем библиотеку Supabase…";
  setView("library");
  const { data, error } = await cloudClient.from("books").select("book").order("updated_at", { ascending: false });
  if (error) {
    books = [];
    renderLibrary();
    $("#storage-status").textContent = "Ошибка подключения к Supabase.";
    showToast(`Не удалось загрузить книги из Supabase: ${error.message}`, true);
    return;
  }
  books = (data || []).map((row) => row.book).filter((book) => book && typeof book === "object");
  try {
    localStorage.setItem(storageKey, JSON.stringify(books));
  } catch {
    showToast("Книги загружены из Supabase, но локальный кеш не сохранился.", true);
  }
  $("#storage-status").textContent = `Библиотека Supabase · ${session.user.email || "аккаунт"}`;
  renderLibrary();
}

async function deleteCloudBook(bookId) {
  if (!cloudClient || !cloudUserId) return true;
  try {
    const { error } = await cloudClient.from("books").delete().eq("id", bookId);
    if (error) throw error;
    return true;
  } catch (error) {
    showToast(`Не удалось удалить книгу из Supabase: ${error.message || error}`, true);
    return false;
  }
}

function bindEvents() {
  const showCreate = () => { $("#brief-form").reset(); setView("create"); };
  $("#nav-library").addEventListener("click", () => { renderLibrary(); setView("library"); });
  $("#nav-create").addEventListener("click", showCreate);
  $("#top-create").addEventListener("click", showCreate);
  $("#empty-create").addEventListener("click", showCreate);
  $("#empty-start").addEventListener("click", showCreate);
  $("#back-library").addEventListener("click", () => { renderLibrary(); setView("library"); });
  $("#book-back").addEventListener("click", () => { renderLibrary(); setView("library"); });
  $("#plan-back").addEventListener("click", () => { renderBook(); setView("book"); });
  $("#cancel-plan").addEventListener("click", () => { renderBook(); setView("book"); });
  $("#editor-back").addEventListener("click", () => { saveChapter(false); renderBook(); setView("book"); });
  $("#brief-form").addEventListener("submit", (event) => { event.preventDefault(); createBook(event.currentTarget); });
  $("#auth-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void signIn($("#auth-email").value.trim(), $("#auth-password").value);
  });
  $("#sign-up").addEventListener("click", () => {
    if (!$("#auth-form").reportValidity()) return;
    void signIn($("#auth-email").value.trim(), $("#auth-password").value, true);
  });
  $("#sign-out").addEventListener("click", async () => {
    if (!cloudClient) return;
    const { error } = await cloudClient.auth.signOut();
    if (error) showToast(`Не удалось выйти: ${error.message}`, true);
  });
  $("#approve-plan").addEventListener("click", () => {
    const book = getActiveBook();
    if (!book) return;
    if (book.chapters.some((chapter) => !chapter.title.trim())) return showToast("У каждой главы должно быть название.", true);
    book.planApproved = true;
    book.updatedAt = Date.now();
    persist();
    renderBook();
    setView("book");
    showToast("План утверждён. Откройте главу, чтобы написать её.");
  });
  $("#edit-plan").addEventListener("click", () => { renderPlan(); setView("plan"); });
  $("#regenerate-plan").addEventListener("click", () => {
    const book = getActiveBook();
    if (!book) return;
    if (!window.confirm("Заменить план? Тексты уже написанных глав останутся сохранены, но могут не соответствовать новому плану.")) return;
    book.chapters = makeOutline(book);
    book.planApproved = false;
    persist();
    renderPlan();
    $("#plan-source").textContent = "Запрашиваем новый план у ИИ…";
    void generateOutline(book);
  });
  $("#save-chapter").addEventListener("click", () => saveChapter());
  $("#chapter-text").addEventListener("input", () => { updateEditorCount(); saveChapter(false); });
  $("#editor-title").addEventListener("input", () => saveChapter(false));
  $("#chapter-note").addEventListener("input", () => saveChapter(false));
  $("#regenerate-chapter").addEventListener("click", writeChapter);
  $("#generate-all").addEventListener("click", generateAllChapters);
  $("#download-book").addEventListener("click", downloadPdf);
  $("#export-text").addEventListener("click", downloadText);
  $("#delete-book").addEventListener("click", () => { $("#confirm-modal").hidden = false; });
  $("#cancel-delete").addEventListener("click", () => { $("#confirm-modal").hidden = true; });
  $("#confirm-modal").addEventListener("click", (event) => { if (event.target === $("#confirm-modal")) $("#confirm-modal").hidden = true; });
  $("#confirm-delete").addEventListener("click", async () => {
    const deletedBookId = activeBookId;
    clearTimeout(cloudSaveTimer);
    if (!(await deleteCloudBook(deletedBookId))) return;
    books = books.filter((book) => book.id !== deletedBookId);
    activeBookId = null;
    persist();
    $("#confirm-modal").hidden = true;
    renderLibrary();
    setView("library");
    showToast("Книга удалена.");
  });
}

async function initializeApp() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error(`Ошибка конфигурации сайта (${response.status}).`);
    const config = await response.json();
    $("#api-dot").classList.toggle("ready", Boolean(config.providerConfigured));
    $("#api-dot").classList.toggle("offline", !config.providerConfigured);
    $("#api-status").textContent = config.providerConfigured
      ? `LLM: ${config.provider}`
      : `${config.provider}: проверьте настройки`;
    if (config.providerError) showToast(config.providerError, true);

    cloudEnabled = Boolean(config.supabaseUrl || config.supabaseAnonKey);
    if (cloudEnabled) {
      if (!config.supabaseUrl || !config.supabaseAnonKey) {
        $("#auth-note").textContent = "Supabase настроен не полностью: задайте URL проекта и anon key.";
        $("#storage-status").textContent = "Ошибка конфигурации Supabase.";
        setView("auth", "Вход");
        return;
      }
      if (!window.supabase?.createClient) {
        $("#auth-note").textContent = "Не загрузилась библиотека Supabase JS. Проверьте подключение к интернету.";
        setView("auth", "Вход");
        return;
      }
      cloudClient = window.supabase.createClient(config.supabaseUrl, config.supabaseAnonKey);
      cloudClient.auth.onAuthStateChange((_event, session) => {
        setTimeout(() => { void handleCloudSession(session); }, 0);
      });
      const { data, error } = await cloudClient.auth.getSession();
      if (error) throw new Error(`Supabase Auth: ${error.message}`);
      await handleCloudSession(data.session);
    } else {
      cloudEnabled = false;
      storageKey = STORAGE_KEY;
      books = loadBooks();
      $("#storage-status").textContent = "Книги хранятся в этом браузере.";
      $("#sign-out").hidden = true;
      renderLibrary();
      setView("library");
    }
  } catch (error) {
    $("#api-dot").classList.add("offline");
    $("#api-status").textContent = "Сервер не запущен";
    $("#storage-status").textContent = "Не удалось подключиться к серверу.";
    showToast(error.message || "Не удалось загрузить конфигурацию сайта.", true);
  }
}

bindEvents();
renderLibrary();
void initializeApp();
