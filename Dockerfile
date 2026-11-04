# Hugging Face Spaces（Docker方式）でアプリを動かすための設定
# 手元の開発環境と同じ Python 3.15 を使う
FROM python:3.15-slim

# HF Spaces はユーザーID 1000 でアプリを動かすため、同じIDの利用者を作る
# （利用記録 usage.db をアプリのフォルダに書き込めるようにするため）
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR $HOME/app

# 先にライブラリだけ入れる（コードだけ変えたときの再ビルドを速くするため）
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリのコード一式をコピーする
COPY --chown=user . .

# HF の Streamlit テンプレートが使う番号（README 先頭の app_port と合わせる）
EXPOSE 8501
# HF はアプリを別ページの中に埋め込んで表示するため、XSRF保護が有効だと
# ファイル添付（Excel）が拒否される。HF上でだけ無効にする（手元の実行は有効のまま）
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.enableXsrfProtection=false"]
