# AItuber 実装計画

## 目的

このリポジトリは、`AI-character/day4` を独立させた AItuber 用の新しい開発ベースとする。

現時点では day4 の機能を引き継ぎつつ、次の方向へ段階的に拡張する。

- `api_chat.py` の責務分解
- 明示的 state machine の導入
- background agent による非同期補助機能の追加
- リアルタイム会話の低レイテンシ維持
- 将来の VLM / 外部検索 / 配信連携への拡張余地の確保

## 現状認識

day4 ベースは、単一ターンの会話アプリとしては十分動作している。

- WebSocket によるテキストストリーミング
- 文単位 TTS ストリーミング
- 会話履歴の保持と assistant 要約
- waiting lipsync
- YouTube Live コメント投入

一方で、会話ターンの責務が `app/api_chat.py` に集中している。

- 入力検証
- 会話履歴更新
- prompt 組み立て
- LLM ストリーミング
- TTS キュー制御
- 要約生成
- WebSocket 送信
- エラー時 rollback

今後、外部イベントや自律動作を増やすには、この集約構造を先にほぐす必要がある。

## 採用方針

### 1. まずは明示的 state machine を導入する

現段階では、いきなり全面 LangGraph 化や全面 MCP 化はしない。

理由は次の通り。

- 主経路のボトルネックは LLM API 呼び出しではなく turn orchestration にある
- リアルタイム会話では決定性とデバッグ性が重要
- 既存 day4 の資産を活かした段階移行がしやすい

したがって、最初は自前の明示的 state machine で会話主経路を整理する。

### 2. background agent は主経路から分離する

自律的な検索、外部監視、follow-up 提案は background agent 側へ分離する。

- 主経路はユーザー入力に即応する
- background agent は会話や外部イベントを監視する
- 実際にしゃべる権限は runtime だけが持つ

background agent は直接 WebSocket や TTS を触らない。
必ず proposal を runtime へ渡し、runtime が採用可否を決める。

### 3. hot path では function call を必須にしない

主経路の初動を止めないことを優先する。

- 通常会話の返答は通常 LLM がそのまま生成する
- 外部検索が必要そうなケースは background agent が後追いで処理する
- follow-up が自然なら、主経路で短い代替文を挟まなくてもよい

function call は必要なら background agent 内部で使う。
少なくとも、主経路の front gate としては必須にしない。

### 4. MCP は後段で検討する

MCP 化の候補は、主経路よりも周辺機能にある。

- 外部検索
- YouTube / 配信コメント取得
- 将来の VLM 入力
- キャラクター管理の一部

LLM 本文生成や TTS の hot path は、当面は直接呼び出しを維持する。

## 目標アーキテクチャ

### 主経路

- `ChatSessionRuntime`
  - 会話 1 本ぶんの実行管理
  - 状態管理
  - 発話キュー管理
  - proposal 採否判断

- `ChatTurnStateMachine`
  - state と event のみを持つ
  - 実処理は runner / worker に委譲する

- `PromptBuilder`
  - system prompt
  - 履歴
  - assistant 履歴要約
  - 将来の外部補足コンテキスト

- `LLMStreamRunner`
  - 通常 LLM のストリーム開始と chunk 取得

- `AudioPipeline`
  - sentence segmenter
  - TTS queue
  - audio event 送信

- `SummaryWorker`
  - 応答完了後の履歴要約生成

- `ChatEventDispatcher`
  - WebSocket 送信の一元化

### 背景経路

- `BackgroundAgentManager`
  - 会話単位の監視 agent 起動管理

- `ConversationObserver`
  - user message
  - assistant message
  - YouTube comment
  - 将来の VLM event
  - timer event
  を購読する

- `ProposalProducer`
  - follow-up 発話候補
  - 外部検索要求
  - 優先度付き補助提案
  を生成する

- `ToolWorker`
  - weather lookup
  - news lookup
  - 将来の外部検索
  を非同期で実行する

## state machine 方針

最初の段階では、会話ターンを次の状態へ分ける。

- `idle`
- `validating_request`
- `preparing_prompt`
- `streaming_text`
- `streaming_audio`
- `summarizing`
- `completed`
- `failed`
- `cancelled`

主要 event は次を想定する。

- `user_message_received`
- `request_validated`
- `prompt_prepared`
- `llm_stream_started`
- `llm_first_chunk_received`
- `llm_stream_finished`
- `audio_segment_enqueued`
- `audio_pipeline_finished`
- `summary_started`
- `summary_finished`
- `proposal_received`
- `turn_failed`
- `client_disconnected`

重要なのは、state machine に外部 API 呼び出しを直書きしないこと。
state machine は遷移だけを担当し、実処理は別コンポーネントへ分離する。

## background agent 方針

background agent は、主経路の返答生成を止めずに、後追いで情報補完する役割を持つ。

例:

1. ユーザーが天気を聞く
2. 通常 LLM は自然な返答をそのまま生成する
3. background agent は weather lookup が必要かを判断する
4. 必要なら tool を実行する
5. 結果を `follow-up proposal` として runtime へ渡す
6. runtime は発話中なら保留し、空いたタイミングで follow-up を追加する

### proposal の原則

- background agent は直接発話しない
- proposal には優先度と期限を持たせる
- runtime は会話状態を見て採用または破棄する
- 同時に複数 proposal があっても runtime が順序を決める

### follow-up の原則

- すでに流した返答を原則訂正しない
- 補足情報として追加発話する
- 主経路より高頻度に割り込まない
- UI 上で main reply と follow-up を区別できるようにする

## LLM 利用方針

### 通常 LLM

- キャラクターとしての通常返答生成
- 主経路のテキストストリーミング
- 必要に応じた履歴要約生成
- キャラクター登録名のローマ字化

### 軽量 LLM

将来的に必要なら導入する。

用途候補:

- 低遅延 intent 判定
- 外部情報要求の分類
- background agent 内の軽量ルーティング

ただし、初期段階では必須にしない。
まずは通常 LLM 主体で構造整理を優先する。

## function call / structured output の扱い

現時点の方針は次の通り。

- 主経路の開始前に function call 判定待ちはしない
- function call が必要なら background agent 内部で使う
- runtime の採否ロジックは prompt に委ねず、明示ロジックで持つ

つまり、制御は runtime、自然言語生成は LLM、tool 選択補助は background agent 内の structured output で行う。

## 実装フェーズ

### Phase 0: ベース整備

- AItuber リポジトリへ day4 を独立移植
- README と名称の整理
- 起動確認手順の更新

### Phase 1: `api_chat.py` の責務分解

最優先フェーズ。

- `ChatEventDispatcher` を分離
- `SummaryWorker` を分離
- `AudioPipeline` を分離
- `LLMStreamRunner` を分離
- `handle_chat_turn` を薄くする

この段階では、外部仕様は極力変えない。

### Phase 2: 明示的 state machine 導入

- state enum 定義
- event 定義
- runtime context 定義
- 既存 `handle_chat_turn` の内部フローを state transition へ移す

この段階でも、まずは 1 ターン完結型のままでよい。

### Phase 3: proposal queue 導入

- 会話単位 proposal queue
- proposal schema 定義
- runtime に follow-up 採否ロジックを追加
- UI へ follow-up メッセージ種別を追加

### Phase 4: background agent の最小実装

- 会話監視 task
- 単一ユースケースでの follow-up 実装
  - 例: weather lookup
- tool 実行結果から follow-up proposal を生成

最初から複数 tool を増やさない。
単一ケースで制御の正しさを確認する。

### Phase 5: 将来拡張

- 軽量 LLM による agent 内分類
- VLM event 連携
- YouTube 以外の外部イベントソース追加
- MCP server 化の検討
- LangGraph 導入の再評価

## 直近の実装優先順位

今すぐ着手する順序は次の通り。

1. リポジトリ名と README を AItuber 前提へ整理する
2. `app/api_chat.py` の責務を分ける
3. state enum / event enum / runtime context を導入する
4. proposal queue のデータ構造を決める
5. weather lookup を題材に background agent の最小版を入れる

## 非目標

現時点では次を急がない。

- 全面 LangGraph 化
- 全面 MCP 化
- skill ベースの大規模自律計画
- 主経路の function call 必須化
- 既存 UI の全面刷新

これらは、主経路の state 管理と background proposal 制御が安定してから検討する。

## 期待する到達点

この計画に沿って進めることで、AItuber は次の状態を目指す。

- 通常会話は低遅延で自然に返せる
- 外部情報は後追い follow-up で自然に補完できる
- runtime が一元的に発話と TTS を管理できる
- 自律的な補助機能を増やしても主経路が壊れにくい
- 将来の配信連携、VLM、複数イベント統合へ拡張しやすい