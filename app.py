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

    for line in lines:
        stripped = line.strip()

        # Track code fences — don't touch anything inside them
        if re.match(r"^(`{3,}|~{3,})", stripped):
            in_code = not in_code
            out.append(line)
            continue

        if in_code:
            out.append(line)
            continue

        # Fix bold text used as heading: **1.2 Title** alone on a line
        m = re.match(r"^\*\*(\d[\d.]* .+?)\*\*\s*$", line)
        if m:
            text = m.group(1).strip()
            dots = text.split()[0].rstrip(".").count(".")
            level = min(dots + 2, 4)
            out.append("#" * level + " " + text)
            continue

        # Fix bare URLs — wrap in <> if not already inside <>, (), [], or backticks
        line = re.sub(
            r"(?<![<(\[`])https?://[^\s<>()\[\]`\"\']+",
            lambda m: f"<{m.group()}>",
            line,
        )

        out.append(line)

    # Add Table: caption to tables that don't have one
    out2 = []
    table_n = 0
    for line in out:
        if re.match(r"^\s*\|.+\|", line):
            prev = next((l for l in reversed(out2) if l.strip()), "")
            if not re.match(r"^\s*Table:", prev):
                table_n += 1
                if out2 and out2[-1].strip():
                    out2.append("")
                out2.append(f"Table: Таблица {table_n}")
                out2.append("")
        out2.append(line)

    text = "\n".join(out2)

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


# ── UI ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="MD → DOCX", page_icon="📄")
st.title("📄 Markdown → Word")
st.caption("Вставьте ссылку на `.md` файл на GitHub — скачайте `.docx`")

url = st.text_input(
    "Ссылка на файл",
    placeholder="https://github.com/user/repo/blob/main/doc.md",
)

reference_file = st.file_uploader(
    "reference.docx — необязательно (для кастомных стилей)",
    type=["docx"],
)

auto_fix = st.checkbox(
    "Автоисправление форматирования",
    value=True,
    help=(
        "Исправляет типичные ошибки:\n"
        "• **жирный текст** вместо заголовков → ## Заголовок\n"
        "• URL без угловых скобок → <https://...>\n"
        "• таблицы без подписи → добавляет Table: Таблица N\n"
        "• пробелы вокруг списков"
    ),
)

st.divider()

if st.button("Конвертировать", type="primary", disabled=not url.strip()):
    with st.spinner("Скачиваю файл..."):
        try:
            md_text = fetch_markdown(url)
        except requests.HTTPError as e:
            st.error(f"Не удалось скачать файл: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Ошибка при загрузке: {e}")
            st.stop()

    with st.spinner("Конвертирую в .docx..."):
        try:
            ref_bytes = reference_file.read() if reference_file else None
            docx_bytes = convert_to_docx(md_text, ref_bytes, auto_fix)
        except Exception as e:
            st.error(f"Ошибка конвертации: {e}")
            st.stop()

    filename = url.rstrip("/").split("/")[-1].removesuffix(".md") + ".docx"
    st.success("Готово!")
    st.download_button(
        label="⬇️ Скачать .docx",
        data=docx_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
