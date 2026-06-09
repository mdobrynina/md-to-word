import re
import os
import tempfile

import requests
import streamlit as st
import pypandoc


def github_to_raw(url: str) -> str:
    # https://github.com/user/repo/blob/branch/path/file.md
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/(.+)", url.strip())
    if match:
        user, repo, path = match.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{path}"
    return url.strip()


def fetch_markdown(url: str) -> str:
    raw_url = github_to_raw(url)
    resp = requests.get(raw_url, timeout=15)
    resp.raise_for_status()
    return resp.text


def convert_to_docx(md_text: str, reference_bytes: bytes | None) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
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
    "reference.docx — необязательно (для кастомных стилей: заголовки, Caption, Source Code и т.д.)",
    type=["docx"],
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
            docx_bytes = convert_to_docx(md_text, ref_bytes)
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
