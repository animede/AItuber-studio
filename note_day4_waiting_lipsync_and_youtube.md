# Day4で進めた「待機動画ベース口パク」と「YouTube Liveコメント連携」の実装

Day3 では、AIキャラ会話アプリの並列化とキャラクタ登録機能を整えました。

Day4 ではその土台を活かしつつ、次の 2 つを大きく進めています。

- waiting 動画ベースの口パク表現を入れたこと
- YouTube Live コメントを会話入力として流し込めるようにしたこと

今回の記事で書きたいのは、単に機能を増やした話ではありません。

AIキャラの返答を、

- 画面上で自然にしゃべっているように見せること
- 外から流れ込むコメントまで会話体験に取り込むこと

の 2 方向から強化した、という話です。

Day4 は、LLM の返答をよりキャラクターらしい体験へ寄せていく段階でした。

## Day4 で解決したかったこと

Day3 の時点でも、文字ストリーミング、文区切り TTS、キャラクタ管理、画像や動画の切り替えまではできていました。

ただ、ここで次の課題が見えていました。

- talking 動画へ切り替えるだけだと、口の動きと音声の一致感が弱い
- 待機中の動画を活かしたまま、しゃべっている感じを出したい
- チャット入力だけでなく、配信コメントもキャラ会話へ取り込みたい
- コメント取得を入れても、既存の会話設計を壊したくない

つまり Day4 のテーマは、

「キャラの見た目の反応を自然にすること」

と、

「会話入力の入口を増やすこと」

の 2 本柱でした。

## 今回追加した大きな差分

先に要点を並べると、Day4 の差分は次のようになります。

### 1. 待機動画ベースの口パク

- waiting 用の mouthless 動画を常時再生する
- `mouth_track.json` に従って口の位置と回転を決める
- mouth sprite を上から重ねる
- 実際の音声再生レベルに応じて口形を切り替える

### 2. lipsync 用 manifest API の追加

- waiting 動画 URL
- mouth sprite の URL 群
- `mouth_track.json`

をまとめて返す API を追加し、フロントエンドがまとめて解釈できるようにしました。

### 3. YouTube Live コメントの取り込み

- 配信 URL または videoId を入力して取得開始できる
- `pytchat` でコメントを取得する
- conversation 単位でセッションを持つ
- 新着コメントだけを順番に user 入力として流し込む

### 4. 既存 UI への自然な統合

- WebSocket 会話処理はそのまま使う
- YouTube コメントは REST + polling で追加する
- 既存の入力フォームと会話履歴の流れにそのまま乗せる

ここで重要なのは、Day4 は大改修ではないことです。

Day3 の構成を壊さず、差分として lipsync と YouTube 連携を足している点がポイントです。

## Day4 の本題 1. なぜ待機動画ベースにしたのか

最初に考えたのは、単純に talking 動画へ切り替える方式の限界でした。

talking 動画は実装が分かりやすい反面、

- 音声の長さにぴったり合わない
- 文ごとの抑揚や間に追従しにくい
- 待機から会話への切り替え感が強く出すぎる

という問題があります。

特に AI キャラ会話では、返答がストリーミングで少しずつ進みます。そこに固定の talking 動画を重ねると、「しゃべっている感」より「動画を切り替えている感」の方が勝ちやすくなります。

そのため Day4 では発想を変えて、

- ベースはずっと waiting 動画
- その上に口だけを動的に重ねる

という方式にしました。

これなら、体の動きや髪の揺れのような待機モーションは維持したまま、口だけを音声に合わせて動かせます。

## lipsync の素材をどう扱ったか

この方式を成立させるには、キャラごとに次の素材が必要です。

- waiting 用 mouthless 動画
- `mouth_track.json`
- `closed.png`
- `half.png`
- `open.png`
- 必要なら `e.png`, `u.png`

ここで `mouth_track.json` は、動画のどの位置に口を置くかをフレームごとに持つためのデータです。

つまり Day4 の口パクは、単に中央に口画像を貼っているのではありません。
動画の進行に合わせて、

- 口の中心位置
- 幅
- 高さ
- 回転

を毎フレーム変えています。

これによって、顔の向きや揺れがある waiting 動画にも追従できます。

## backend では manifest API を追加した

フロントエンドで lipsync を描くには、動画、sprite、track を個別に探すより、ひとまとめの情報を受け取れる方が扱いやすいです。

そこで Day4 では、キャラごとの lipsync manifest を返す API を追加しました。

概念的には次のような情報を返しています。

```python
return {
    "available": available,
    "character_id": character_id,
    "waiting_video_url": f"/api/characters/{character_id}/lipsync/waiting-video" if waiting_video_file else None,
    "sprite_urls": sprite_urls,
    "track": track_payload,
}
```

ここでのポイントは、単にファイルを置くだけでなく、フロントエンドがそのまま使える形まで backend 側で正規化していることです。

また、required sprite と optional sprite を分けていて、最低限 `closed`, `half`, `open` が揃っていれば lipsync を有効にできます。

これにより、素材が全部揃っていないキャラでも、利用可能かどうかを API 側で明確に判断できます。

## frontend では manifest をキャッシュする

フロントエンド側では、キャラクターごとに lipsync manifest を読み込み、状態として保持します。

```javascript
const request = fetch(`/api/characters/${encodeURIComponent(characterId)}/lipsync/manifest`)
  .then((response) => parseResponseJson(response))
  .then((manifest) => {
    const normalized = {
      ...manifest,
      available: Boolean(manifest.available),
      sprite_urls: manifest.sprite_urls || {},
      track: manifest.track || null,
    };
    state.lipsyncManifests[characterId] = normalized;
    return normalized;
  });
```

ここでキャッシュしている理由は、会話中に毎回 manifest を取りに行く必要がないからです。

待機動画ベースの lipsync は表示のたびに参照されるので、最初に読み込んで保持しておく方が自然です。

## 実際の口パクは Web Audio API の音量で切り替える

Day4 で重要なのは、「今しゃべっているはず」という推定ではなく、実際に再生している音声のレベルを見て口形を切り替えていることです。

フロントエンドでは `AudioContext` と `AnalyserNode` を使って、再生中音声の振幅を取り出しています。

```javascript
state.audioAnalyser.getByteTimeDomainData(state.audioAnalyserData);
let sum = 0;
for (const sample of state.audioAnalyserData) {
  const normalized = (sample - 128) / 128;
  sum += normalized * normalized;
}

const rms = Math.sqrt(sum / state.audioAnalyserData.length);
state.currentAudioLevel = Math.min(1, rms * 4.5);
```

この値を使って、

- 無音に近ければ `closed`
- 少し開いていれば `half`
- 大きく出ていれば `open`
- 余裕があれば `e`, `u`

という形で sprite を切り替えています。

この方式の良いところは、TTS の音声長や発話速度が変わっても、口の動きがある程度追従することです。

## 口の位置合わせは requestAnimationFrame で回している

口は 1 回置けば終わりではありません。
waiting 動画がループし続けるので、動画の現在時刻に合わせて毎フレーム位置を更新する必要があります。

Day4 では `requestAnimationFrame()` を使って、動画の currentTime と `mouth_track.json` を対応づけています。

流れとしては次のようになります。

1. 動画 currentTime から現在フレームを求める
2. `mouth_track.json` の quad 情報を取り出す
3. 口の中心、幅、高さ、回転へ変換する
4. 現在の音声レベルから sprite を決める
5. mouth image の位置と画像を更新する

この方法にしたことで、待機動画を止めずに口だけ自然に動かせるようになりました。

## 既存の見た目切り替えをどう壊さずに入れたか

Day3 までのアプリには、

- initial
- waiting
- talking

の表示モードがすでにありました。

Day4 ではその仕組みを捨てず、waiting モードの描画だけ差し替える形にしています。

```javascript
const canUseLipsync = mode === "waiting" && !isCharacterAssetEditEnabled() && isWaitingLipsyncActive(character.id);
if (canUseLipsync) {
  const manifest = getCharacterLipsyncManifest(character.id);
  return createWaitingLipsyncMedia(manifest, cleanupKey, className, `${character.display_name} の口パク`);
}
```

つまり、全体の状態機械はそのままで、waiting 表示に入った時だけ lipsync 専用描画へ分岐しています。

これは重要な点でした。
大きく作り直すと他の表示モードまで壊しやすいですが、この形なら差分を限定できます。

## Day4 の本題 2. YouTube Live コメントをどう取り込んだか

もう 1 つの柱が、YouTube Live コメントの取り込みです。

ここでやりたかったのは、配信コメントを特別な別画面に出すことではありません。
既存の AI キャラ会話の入口に、そのままコメントを差し込むことでした。

つまり目指したのは、

- ブラウザ上でコメント取得を開始する
- 新着コメントを受信する
- それを通常の user メッセージと同じ流れで送る

という構成です。

## YouTube 連携を REST + polling にした理由

最初に設計で決めたのは、既存の WebSocket を YouTube 用に複雑化しないことでした。

すでに会話本体は WebSocket で動いています。
そこへ YouTube コメント制御まで混ぜると、

- 開始
- 停止
- 新着取得
- 自動送信
- エラー表示

の責務が 1 本のソケットに入りすぎます。

そこで Day4 では、YouTube コメントは REST + polling に分けました。

- `/api/youtube/start`
- `/api/youtube/stop`
- `/api/youtube/comments/{conversation_id}`

という 3 つで制御し、フロントエンドが一定間隔で新着を取りに行く形です。

この構成にしたおかげで、既存の WebSocket 会話処理にはほとんど手を入れずに済みました。

## URL と videoId の両方を受けるようにした

使い勝手の面では、YouTube の入力を生の videoId に限定しないことも重要でした。

そのため backend 側では、

- 通常の watch URL
- `youtu.be` の短縮 URL
- `/live/`
- `/embed/`
- 生の videoId

を解釈できるようにしています。

```python
if "youtube.com" in candidate or "youtu.be" in candidate:
    patterns = [
        r"[?&]v=([A-Za-z0-9_-]{6,})",
        r"youtu\.be/([A-Za-z0-9_-]{6,})",
        r"/live/([A-Za-z0-9_-]{6,})",
        r"/embed/([A-Za-z0-9_-]{6,})",
    ]
```

これにより UI は「URL または videoId を入れて開始」という、素直な操作にできます。

## コメント取得は conversation 単位で持つ

Day4 の会話設計は、もともと conversation 単位です。
なので YouTube コメント側も、グローバル 1 本ではなく会話ごとにセッションを持たせました。

```python
class YouTubeCommentService:
    def __init__(self) -> None:
        self._sessions: dict[str, YouTubeCommentSession] = {}
```

この設計にすると、

- 会話を作り直した時の停止と再開がやりやすい
- どの会話に紐づくコメント取得かが明確になる
- 既存の会話 ID ベース設計と自然に揃う

という利点があります。

Day4 はあくまで既存アプリの延長なので、この整合性はとても重要でした。

## コメントの重複処理も必要だった

YouTube 側は取得タイミングによって同じコメントが見えることがあります。
そのためセッション側では comment_id を覚えて、二重投入を防いでいます。

また、comment_id が取れない場合のために、

- author_name
- published_at
- message

を組み合わせた fallback key も持たせています。

こうしておかないと、同じコメントが何度も user 発話として流れ込み、会話体験が崩れます。

## frontend 側では pending queue を持つ

受け取ったコメントをその場で即送信すると、LLM が返答中のときにぶつかります。

そのためフロントエンドでは、まず新着コメントを pending queue に積みます。

そして、

- 自動送信が ON
- 現在ストリーミング中ではない

という条件を満たした時だけ、次のコメントを通常の chat 送信経路へ流します。

つまり YouTube コメントは特別扱いせず、最終的には既存の `sendChatMessageText()` に渡しています。

この設計にしたことで、コメント由来の入力でも、通常入力と同じ履歴・同じ表示・同じ TTS フローにそのまま乗ります。

## 実装で詰まった点 1. 起動直後の race condition

今回の YouTube 連携では、取得開始直後に polling が止まってしまう race condition もありました。

原因は、セッション開始 API の返り値で thread の即時生存判定を見ていたことです。
スレッド起動直後はタイミング次第で `is_alive()` が期待通りに見えず、フロントが「まだ動いていない」と判断する余地がありました。

そこで Day4 では、開始 API 成功後はフロント側でまず取得開始状態に入り、そのまま最初の polling を走らせるようにしています。

この修正で、開始直後に止まった扱いになる問題を避けられました。

## 実装で詰まった点 2. pytchat と thread の相性

もう 1 つ、今回かなり実務的だったのが `pytchat` の扱いです。

`pytchat.create()` は既定で signal handler を登録しようとします。
ところが Day4 では、コメント取得を worker thread 側で初期化しています。

この組み合わせだと、Python の制約で次のエラーが出ます。

```text
signal only works in main thread of the main interpreter
```

これに対しては、次のように `interruptable=False` を明示することで回避しました。

```python
livechat = pytchat.create(video_id=self.video_id, interruptable=False)
```

こういう点は記事にすると地味ですが、実装上はとても重要です。
ライブラリをそのまま呼べば済むわけではなく、どのスレッドで初期化するのかまで考える必要がありました。

## Day4 で得られたもの

今回の Day4 で大きかったのは、AIキャラの体験が次の 2 方向で広がったことです。

### 1. 返答の見え方が自然になった

waiting 動画を維持したまま口だけ動かせるようになったことで、単なる talking / waiting 切り替えより自然に見えるようになりました。

これは派手な演出というより、キャラがそこに居続けたまま話し始める感覚に近いです。

### 2. 会話の入口が広がった

配信コメントを user 入力として取り込めるようになったことで、アプリが単なる 1 対 1 チャットから、配信文脈のある AI キャラへ近づきました。

入力欄だけでなく外部コメントも会話へ流れ込むので、キャラが場に反応している感じが出やすくなります。

## 今回作ったものの本質

Day4 は、機能を足した回というより、

「キャラが画面の中で存在している感じ」

と、

「キャラが外の場に反応している感じ」

を足した回だったと思っています。

waiting 口パクは前者です。
YouTube Live コメント連携は後者です。

この 2 つが入ると、同じ LLM を使っていても、単なるチャットツールではなく、キャラクターが場に居るアプリへ一段近づきます。

## 次にやりたいこと

Day4 の段階でも、まだ伸ばせる余地はあります。

- talking 動画側にも lipsync を広げる
- 口形の種類を増やす
- YouTube コメントの表示 UI を強化する
- コメントの優先度や拾い方を調整する
- 配信者コメントやスーパーチャットの扱いを分ける

ただ、Day4 の時点で大事だったのは、まず待機動画ベース lipsync と YouTube コメント流入の最小構成を、既存アプリを壊さず成立させることでした。

そこは、かなり手応えのある形まで来ています。

Day3 で作った「会話アプリの土台」に対して、Day4 ではようやく

- 見た目の反応
- 外部入力との接続

が乗ってきました。

AI キャラを、ただ返答する存在ではなく、配信や画面の中で動いて反応する存在にしていくうえで、Day4 は大きな一歩だったと思っています。
