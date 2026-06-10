import re
import os
import base64
import tempfile

import requests
import streamlit as st
import pypandoc


def github_to_raw(url: str) -> str:
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/(.+)", url.strip())
    if match:
        user, repo, path = match.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{path}"
    return url.strip()


def mermaid_to_ink_url(code: str) -> str:
    encoded = base64.urlsafe_b64encode(code.encode("utf-8")).decode("utf-8")
    return f"https://mermaid.ink/img/{encoded}"


def normalize_markdown(md_text: str) -> str:
    """Fix common formatting issues before conversion."""
    lines = md_text.splitlines()
    out = []
    in_code = False

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # Track code fences — don't modify anything inside them
        fence = re.match(r"^(`{3,}|~{3,})(\w*)", stripped)
        if fence:
            # Add language tag 'text' to bare code fences
            if not in_code and fence.group(2) == "":
                line = fence.group(1) + "text"
            in_code = not in_code
            out.append(line)
            continue

        if in_code:
            out.append(line)
            continue

        # Fix missing space after # in headings: #Title → # Title
        line = re.sub(r"^(#{1,6})([^# \n])", r"\1 \2", line)

        # Fix bold text used as heading: **1.2 Title** alone on a line
        m = re.match(r"^\*\*(\d[\d.]* .+?)\*\*\s*$", line)
        if m:
            text = m.group(1).strip()
            dots = text.split()[0].rstrip(".").count(".")
            level = min(dots + 2, 4)
            out.append("#" * level + " " + text)
            continue

        # Convert HTML inline tags to markdown equivalents
        line = re.sub(r"<br\s*/?>", "  \n", line)
        line = re.sub(r"<(strong|b)>(.*?)</(strong|b)>", r"**\2**", line, flags=re.IGNORECASE)
        line = re.sub(r"<(em|i)>(.*?)</(em|i)>", r"*\2*", line, flags=re.IGNORECASE)
        line = re.sub(r"<(s|del)>(.*?)</(s|del)>", r"~~\2~~", line, flags=re.IGNORECASE)
        line = re.sub(r"<code>(.*?)</code>", r"`\1`", line, flags=re.IGNORECASE)
        line = re.sub(r"<u>(.*?)</u>", r"\1", line, flags=re.IGNORECASE)
        # Strip remaining HTML tags (but keep content)
        line = re.sub(r"</?(?!mermaid)[a-zA-Z][^>]*>", "", line)

        # Fix bare URLs — wrap in <> if not already inside <>, (), [], or backticks
        line = re.sub(
            r"(?<![<(\[`])https?://[^\s<>()\[\]`\"\']+",
            lambda m: f"<{m.group()}>",
            line,
        )

        out.append(line)

    # Add blank line after headings if missing
    out2 = []
    for i, line in enumerate(out):
        out2.append(line)
        if re.match(r"^#{1,6} ", line):
            next_line = out[i + 1] if i + 1 < len(out) else ""
            if next_line.strip():
                out2.append("")

    # Wrap bare image captions in custom-style Caption block
    # Pattern: image line, blank line, line like "Рисунок N –..." or "Figure N"
    out3 = []
    i = 0
    while i < len(out2):
        line = out2[i]
        if (re.match(r"^\s*!\[.*\]\(.*\)\s*$", line)
                and i + 2 < len(out2)
                and out2[i + 1].strip() == ""
                and re.match(r"^\s*(Рисунок|Рис\.|Figure|Fig\.)\s*\d", out2[i + 2], re.IGNORECASE)
                and not re.match(r"^\s*:::", out2[i + 2])):
            caption = out2[i + 2].strip()
            out3.append(line)
            out3.append("")
            out3.append('::: {custom-style="Caption"}')
            out3.append(caption)
            out3.append(":::")
            i += 3
            continue
        out3.append(line)
        i += 1

    # Add Table: caption to tables that don't have one
    out4 = []
    table_n = 0
    for line in out3:
        if re.match(r"^\s*\|.+\|", line):
            prev = next((l for l in reversed(out4) if l.strip()), "")
            if not re.match(r"^\s*Table:", prev):
                table_n += 1
                if out4 and out4[-1].strip():
                    out4.append("")
                out4.append(f"Table: Таблица {table_n}")
                out4.append("")
        out4.append(line)

    text = "\n".join(out4)

    # Ensure blank line before list items
    text = re.sub(r"(?<=\S)\n([ \t]*(?:[-*+]|\d+\.)[ \t])", r"\n\n\1", text)

    # Collapse 3+ blank lines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text


def preprocess_markdown(md_text: str) -> str:
    def replace_mermaid(match):
        code = match.group(1).strip()
        return f"![]({mermaid_to_ink_url(code)})"

    return re.sub(r"```mermaid\s*\n(.*?)```", replace_mermaid, md_text, flags=re.DOTALL)


def fetch_markdown(url: str) -> str:
    raw_url = github_to_raw(url)
    resp = requests.get(raw_url, timeout=15)
    resp.raise_for_status()
    return resp.text


def download_images(md_text: str, tmp_dir: str) -> str:
    img_dir = os.path.join(tmp_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

    def replace_image(match):
        alt = match.group(1)
        url = match.group(2)
        if not url.startswith("http"):
            return match.group(0)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            ext = url.split("?")[0].split(".")[-1] or "png"
            fname = base64.urlsafe_b64encode(url.encode()).decode()[:40] + f".{ext}"
            local_path = os.path.join(img_dir, fname)
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"![{alt}](images/{fname})"
        except Exception:
            return match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_image, md_text)


def convert_to_docx(md_text: str, reference_bytes: bytes | None, auto_fix: bool) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        if auto_fix:
            md_text = normalize_markdown(md_text)
        md_text = preprocess_markdown(md_text)
        md_text = download_images(md_text, tmp)

        md_path = os.path.join(tmp, "input.md")
        docx_path = os.path.join(tmp, "output.docx")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_text)

        extra_args = ["--standalone"]
        if reference_bytes:
            ref_path = os.path.join(tmp, "reference.docx")
            with open(ref_path, "wb") as f:
                f.write(reference_bytes)
            extra_args.append(f"--reference-doc={ref_path}")

        pypandoc.convert_file(md_path, "docx", outputfile=docx_path, extra_args=extra_args)

        with open(docx_path, "rb") as f:
            return f.read()


REFERENCE_URL = "https://raw.githubusercontent.com/mdobrynina/md-to-word/main/reference.docx"


@st.cache_data(show_spinner=False)
def load_default_reference() -> bytes | None:
    try:
        resp = requests.get(REFERENCE_URL, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


# ── UI ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="MD → DOCX", page_icon="📄")
st.title("📄 Markdown → Word")

tab_url, tab_file = st.tabs(["🔗 Ссылка на GitHub", "📁 Загрузить файл"])

with tab_url:
    url = st.text_input(
        "Ссылка на файл",
        placeholder="https://github.com/user/repo/blob/main/doc.md",
    )

with tab_file:
    uploaded_md = st.file_uploader("Выберите .md файл", type=["md", "markdown", "txt"])

st.divider()

with st.expander("Настройки стилей"):
    st.caption(
        "По умолчанию применяется встроенный reference.docx из репозитория. "
        "Загрузите свой файл, чтобы переопределить стили."
    )
    col1, col2 = st.columns([2, 1])
    with col1:
        reference_file = st.file_uploader("Загрузить свой reference.docx", type=["docx"])
    with col2:
        st.write("")
        st.write("")
        default_ref = load_default_reference()
        if default_ref:
            st.download_button(
                "⬇️ Скачать шаблон",
                data=default_ref,
                file_name="reference.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

auto_fix = st.checkbox(
    "Автоисправление форматирования",
    value=True,
    help=(
        "Исправляет типичные ошибки:\n"
        "• **жирный текст** вместо заголовков → ## Заголовок\n"
        "• URL без угловых скобок → <https://...>\n"
        "• таблицы без подписи → добавляет Table: Таблица N\n"
        "• пробелы вокруг списков\n"
        "• HTML теги → markdown\n"
        "• подписи к рисункам → custom-style Caption"
    ),
)

st.divider()

has_input = bool((url and url.strip()) or uploaded_md)
if st.button("Конвертировать", type="primary", disabled=not has_input):
    filename = "document.docx"
    if uploaded_md is not None:
        md_text = uploaded_md.read().decode("utf-8", errors="replace")
        filename = uploaded_md.name.removesuffix(".md").removesuffix(".markdown").removesuffix(".txt") + ".docx"
    else:
        with st.spinner("Скачиваю файл..."):
            try:
                md_text = fetch_markdown(url)
                filename = url.rstrip("/").split("/")[-1].removesuffix(".md") + ".docx"
            except requests.HTTPError as e:
                st.error(f"Не удалось скачать файл: {e}")
                st.stop()
            except Exception as e:
                st.error(f"Ошибка при загрузке: {e}")
                st.stop()

    with st.spinner("Конвертирую в .docx..."):
        try:
            if reference_file:
                ref_bytes = reference_file.read()
            else:
                ref_bytes = load_default_reference()
            docx_bytes = convert_to_docx(md_text, ref_bytes, auto_fix)
        except Exception as e:
            st.error(f"Ошибка конвертации: {e}")
            st.stop()

    st.success("Готово!")
    st.download_button(
        label="⬇️ Скачать .docx",
        data=docx_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
