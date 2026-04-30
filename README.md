# AItuber

開発中

## 概要

このリポジトリは、既存の会話アプリ系統を整理し直した AItuber 用の作業ベースです。
FastAPI をバックエンドに使い、フロントエンドはシンプルな静的 HTML/CSS/JavaScript で構成しています。
Windows と Linux の両方で動かす前提で、Python 側のパス処理は `pathlib.Path`、画像指定はブラウザ向け URL パスで扱う構成にしています。

主な特徴は次のとおりです。

- 軽量な会話管理
- キャラクター定義の分離
- WebSocket によるストリーミング応答
- 文字単位に近い逐次表示
- 文区切りごとの音声ストリーミング
- 句読点でも区切れる TTS セグメント調整
- 履歴圧縮用 assistant 要約の自動生成と、UI からの閾値調整
- 初回文字到達時間のフロント計測とバックエンドログ出力
- キャラクター画像・動画の表示
- waiting 動画ベースの口パク試行を実装
- YouTube Live コメント取得を `pytchat` ベースで試せます
- マイク入力を無音検出で区切って LLM へ直接送信できます

現時点では、既存版の動作を引き継ぎつつ、会話主経路を少しずつ整理しながら検証できる構成にしています。

## 参考資料

`day2` の実装背景として、次の記事を参考にしています。

- [連載:AIキャラの作り方ー（Ｄay-2）](https://note.com/ai_meg/n/n2aeaa96e0245)

## 位置づけ

AItuber は、従来機能を維持しつつ、会話ターン処理の見通しを改善するための作業リポジトリです。

現時点での主な違いは次のとおりです。

- 起動入口は `webapp_main.py` のまま残し、アプリ本体は `app/` から import する形です
- 会話主経路は `api_chat.py` と `chat_session_runtime.py` を中心に追えるようにしています
- 状態遷移は `chat_turn_state_machine.py` で明示化しています
- 既定ポートは `8005` です

## ディレクトリ構成

### バックエンド

- `webapp_main.py`
  - FastAPI アプリの起動入口です。
  - `app/` 配下の API ルータを登録し、`static` ディレクトリを配信します。

- `app/api_chat.py`
  - 会話関連 API を定義します。
  - 会話作成、会話取得、会話クリア、WebSocket 会話の入口を担当します。

- `app/chat_session_runtime.py`
  - 会話 1 ターンぶんの主経路をまとめています。
  - prompt 組み立て、LLM ストリーム、summary 実行、rollback を担当します。

- `app/chat_turn_state_machine.py`
  - 会話ターンの状態と event 遷移を定義します。
  - 実処理は持たず、遷移の明示化だけを担当します。

- `app/audio_pipeline.py`
  - 文区切りセグメントから TTS キューと audio event 送信を担当します。
  - UI の「TTS区切り」が ON のときは、句点だけでなく読点などでも短く区切ります。

- `app/chat_event_dispatcher.py`
  - WebSocket イベント送信とログ出力の一元化を担当します。

- `app/api_meta.py`
  - メタ情報 API を定義します。
  - ヘルスチェックと TTS メタ情報取得を担当します。
  - フロント初期表示で使う要約設定の既定値も返します。

- `app/api_characters.py`
  - キャラクター一覧と保存 API を定義します。
  - キャラクタ登録名ごとの `character.json` 保存、role に書いた名前の LLM ローマ字変換による登録名自動補完、画像アップロード、削除を担当します。
  - `data` 配下の画像・動画は API 経由で配信します。
  - waiting lipsync 用の manifest、mouth sprite、mouthless 動画も配信します。

- `app/llm_client.py`
  - `llama.cpp` の OpenAI 互換 API へ接続するラッパです。
  - 会話生成だけでなく、role に書かれた日本語名のローマ字変換にも使います。
  - 長い assistant 応答を、後続会話向けの短い履歴要約へ圧縮する処理も持ちます。
  - 音声入力があるターンでは、最後の user 発話を OpenAI 互換の `input_audio` 形式へ差し替えて LLM へ渡します。

- `app/conversation_store.py`
  - 会話データをメモリ上で管理します。
  - 会話の作成、取得、メッセージ追加、履歴保持、履歴要約反映を担当します。

- `app/character_registry.py`
  - キャラクター定義を管理します。
  - `data/characters/<登録名>/character.json` を読み書きします。
  - 画像・動画は `data/characters/<登録名>/assets/` に保存します。

- `app/schemas.py`
  - API 入出力で使う Pydantic モデルを定義します。
  - chat payload では `input_audio_b64` と `input_audio_format` も受け取れます。

- `app/settings.py`
  - 接続先 URL、モデル名、履歴件数、ポートなどの設定を管理します。
  - assistant 要約の閾値既定値は 150 文字、要約文字数既定値は 100 文字です。
  - 既定待受ポートは `8005` です。

- `app/api_youtube.py`
  - YouTube Live コメント取得の開始、停止、新着取得 API を定義します。
  - `pytchat` を使い、会話 ID ごとに取得セッションを管理します。

- `app/youtube_comment_service.py`
  - `pytchat` による Live コメント取得処理を担当します。
  - 会話単位でコメント取得スレッドを管理し、新着コメントだけをフロントへ返します。

- `app/stream_segmenter.py`
  - LLM の `delta` を文区切り単位にまとめます。
  - TTS に渡す短いセグメントを切り出します。

- `app/tts_client.py`
  - Aivis / VOICEVOX 互換 TTS API を呼び出します。
  - `audio_query` と `synthesis` を使って WAV を生成します。

### フロントエンド

- `static/index.html`
  - Web UI の本体です。
  - キャラクター表示、会話エリア、入力欄を定義します。

- `static/style.css`
  - UI の見た目とレイアウトを定義します。
  - 会話エリアの内部スクロールもここで制御しています。

- `static/app.js`
  - フロントエンドの状態管理と API 通信を担当します。
  - WebSocket 応答の受信、描画キュー、音声再生キュー、会話表示更新を行います。
  - 履歴件数に加えて、assistant 要約開始文字数と要約文字数を UI から送信できます。
  - マイクボタンの ON/OFF、無音検出、録音区間の WAV 化、audio payload 送信も担当します。
  - ユーザー送信から最初の `delta` 到達までの時間を計測し、フッタに表示します。
  - waiting lipsync があるキャラでは、音声再生中も waiting 動画を維持しつつ mouth sprite を重ねます。
  - YouTube コメント取得が有効なときは、新着コメントを定期取得し、既存の user 入力として会話へ流し込みます。

### アセット

- `data/characters/momo/character.json`
  - ももの設定ファイルです。
  - 他キャラも `data/characters/<登録名>/character.json` の形で追加されます。

- `data/characters/<登録名>/assets/`
  - `main.*`、`talking.*`、`waiting.*` の名前で画像・動画を保存します。
  - ブラウザからは `/api/characters/<登録名>/assets/<種別>` で配信します。

- `data/characters/<登録名>/mouth_track.json`, `mouth/`, `*_waiting_loop_mouthless_h264.mp4`
  - waiting lipsync 用の追加素材です。
  - ブラウザからは `/api/characters/<登録名>/lipsync/*` 経由で参照します。

## パスの扱い

- `visual_path` には、OS の実ファイルパスではなく、ブラウザから参照する URL パスを設定します
- 例: `/static/assets/characters/character.jpg`
- `C:\\images\\momo.jpg` や `/home/user/image.jpg` のようなローカルファイルパスは設定しません
- サーバ内部のファイル参照は `pathlib.Path` を使っているため、Windows と Linux の両方で扱えます

## ストリーミング仕様

会話の送受信は WebSocket で行います。
フロントエンドは `/ws` に接続し、`action: "chat"` を送信すると次のイベントを受信します。

- `start`
  - 応答開始通知
- `delta`
  - 追記文字列
- `audio`
  - 文区切りで生成された音声セグメント
- `end`
  - 応答完了通知
- `error`
  - エラー通知

AItuber でも基本仕様は従来版と同じで、waiting lipsync 素材があるキャラでは `talking` 動画へ切り替えず、音声イベントを基準に waiting 動画上で口パクを重ねます。

TTS区切りを ON にすると、句点ベースだけでなく読点などでも短く区切って TTS へ渡します。

また、初回の `delta` を受け取るまでの時間をフロントで計測し、画面下部の「初回文字」に表示します。

マイク入力を ON にすると、ブラウザ側で無音検出して発話区間を区切り、その区間を `wav` として LLM に直接送信します。

バックエンドの schema や WebSocket payload 形式を変えたあとに「メッセージ形式が不正です。」が出る場合は、古いプロセスが残っていることがあります。その場合はサーバー再起動を先に確認してください。

## 履歴要約設定

AItuber では、長い assistant 応答を履歴用の短い要約へ置き換える機能があります。

- 「要約開始文字数」以上の assistant 応答だけ要約対象になります
- 「要約文字数」で履歴へ残す要約の上限を決めます
- 既定値は 150 / 100 です
- 「要約開始文字数」を `0` にすると履歴要約は無効になります

要約設定は送信ごとに WebSocket payload へ含め、バックエンド側では既定値の配布にも `/api/health` を使います。

## 計測とログ

AItuber では、会話の初動遅延を見やすくするために、フロントとバックエンドの両方でタイミングを確認できます。

- フロントエンド: 送信から最初の `delta` 到達までを「初回文字」として表示
- バックエンド: 最初の LLM チャンク到達時に `llm_first_chunk_timing` を JSON で標準出力へ出力
- バックエンド: 履歴要約を生成したときに `assistant_history_summary` を JSON で標準出力へ出力

## YouTube コメント連携

AItuber では、YouTube Live のコメントを `pytchat` で取得し、既存の user 入力として会話へ流し込めます。

- UI の「YouTube コメント」を ON にする
- 配信 URL または `videoId` を入力する
- 「取得開始」を押す
- 「新着コメントを自動送信する」が ON の場合、受信したコメントを順番に会話へ投入する

この機能は Google API キーを使わず、`pytchat` だけで動かします。
そのため、入力欄は API キーではなく配信 URL / `videoId` を受け取る形にしています。

## 環境導入

AItuber を動かすには、少なくとも次の 3 つが必要です。

- Python 3.10 以上
- `llama.cpp` の OpenAI 互換 API サーバ
- Aivis / VOICEVOX 互換 TTS を使う場合は AivisSpeech Engine

Python 環境は、リポジトリルートで仮想環境を作ってから `requirements.txt` を入れます。

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## LLM サーバの起動

AItuber は既定で `http://127.0.0.1:8080/v1` の OpenAI 互換 API を参照します。先に別ターミナルで `llama-server` を起動しておきます。

```bash
llama-server -hf unsloth/gemma-4-E4B-it-GGUF:Q4_K_M --reasoning off --host 0.0.0.0 --port 8080
```

起動確認:

```bash
curl -s http://127.0.0.1:8080/v1/models
```

## AivisSpeech Engine の起動

音声ストリーミングを使う場合は、AivisSpeech Engine を別ターミナルで起動しておきます。既定接続先は `http://127.0.0.1:10101` です。

起動確認:

```bash
curl -s http://127.0.0.1:10101/version
```

## Web アプリの起動

既定の待受ポートは `8005` です。
`day3` と同じポートで起動したい場合は、環境変数 `APP_PORT=8001` を付けて起動します。

Linux / macOS:

```bash
python webapp_main.py
```

`8001` で起動する場合:

```bash
APP_PORT=8001 python webapp_main.py
```

Windows PowerShell:

```powershell
python webapp_main.py
```

`8001` で起動する場合:

```powershell
$env:APP_PORT = "8001"
python webapp_main.py
```

Windows コマンドプロンプト:

```bat
python webapp_main.py
```

`8001` で起動する場合:

```bat
set APP_PORT=8001
python webapp_main.py
```

仮想環境を有効化せずに直接実行したい場合は、リポジトリルートで次を使います。

Linux / macOS:

```bash
.venv/bin/python webapp_main.py
```

`8001` で起動する場合:

```bash
APP_PORT=8001 .venv/bin/python webapp_main.py
```

Windows PowerShell / コマンドプロンプト:

```bat
.\.venv\Scripts\python.exe webapp_main.py
```

`8001` で起動する場合:

```bat
set APP_PORT=8001
.\.venv\Scripts\python.exe webapp_main.py
```

起動後、ブラウザで次を開きます。

```text
http://127.0.0.1:8005
```

`8001` で起動した場合は次を開きます。

```text
http://127.0.0.1:8001
```

現在このリポジトリでよく使う起動例は次のとおりです。

- LLM サーバ: `http://127.0.0.1:8080/v1`
- Web アプリ: `http://127.0.0.1:8005`
- TTS サーバ: `http://127.0.0.1:10101`

Linux / macOS で、リポジトリルートから直接起動する例:

```bash
TTS_ENABLED=true APP_PORT=8005 .venv/bin/python webapp_main.py
```

Windows PowerShell で TTS を有効にして起動する例:

```powershell
$env:TTS_ENABLED = "true"
$env:APP_PORT = "8005"
python webapp_main.py
```

起動確認:

```bash
curl -s http://127.0.0.1:8005/api/health
```

## 操作方法

基本的な使い方は従来 UI と同じです。

1. ブラウザで `http://127.0.0.1:8005` を開きます。
2. 左側の「キャラクター」で会話相手を選びます。
3. 必要なら「キャラクタロール」を編集して、口調や設定を調整します。
4. 必要なら「履歴件数」「要約開始文字数」「要約文字数」を調整します。
5. 入力欄にメッセージを入れて「送信」します。

送信後の画面挙動:

- assistant の返答は WebSocket でストリーミング表示されます
- 音声ストリーミングが ON のときは、文区切りごとに順番再生されます
- 「TTS区切り」で読点区切りを ON にすると、句点を待たずに「、」などでも短く順番再生されます
- waiting lipsync 素材があるキャラでは `talking.mp4` へ切り替えず、`waiting.mp4` 上に口パクを重ねます
- 画面下部の「初回文字」に、送信から最初の返答文字までの時間が表示されます
- 長い assistant 応答では、設定に応じて履歴用要約がバックエンドで生成されます

## 補足

- 会話履歴はメモリ保持のみです
- サーバ再起動で履歴は消えます
- 認証や永続化はまだ入れていません
- 会話主経路は `api_chat.py` と `chat_session_runtime.py` を読むと追いやすいように整理しています
