# 用 StandRig 做高可动模型

出处：[绘爱さやか @sayaka_aiart](https://x.com/sayaka_aiart/status/2116165983090180419)（2026-10-30）

> psdファイルも配布されていたので  
> StandRigを使ってAIにモデリングしてもらいました  
> 所要時間1時間半  
>
> psdファイルを渡してプロンプトは  
>
> こちらのパーツ分けされたpsdファイルを元に  
> 物理演算も入れた高稼働な2Dモデルを  
> StandRigを使って作成してみて

她用的是已经分好图层的 PSD，不是从零画。提示词只有三行，AI 通过 [StandRig](https://github.com/sayaka-aiart/StandRig) 做网格、变形和物理，大约一个半小时。同一天她还写过：没分层的单张立绘也能让 AI 全自动绑定，但分层和素材本身更花时间。

本仓库把这件事收成文件，不把 StandRig 嵌进直播进程。

| 文件 | 作用 |
|---|---|
| `assets/standrig/PROMPT.txt` | 推文里的原文提示词 |
| `assets/standrig/README.md` | 交接说明 |
| `assets/standrig/工作区/` | 上传或生成形象后自动放好的 PSD + 提示词，可整夹交给 AI |

## 和当前直播页怎么接

1. 工作室上传一张立绘。see-through 远程分层，得到 PSD，再做成网页里能呼吸、眨眼、对口型的形象。这一步完了就可以点「开始直播」。
2. 若已经有分层 PSD，直接上传 PSD，跳过 see-through。
3. 生成成功后，`assets/standrig/工作区/` 里会有这份 PSD 和 `PROMPT.txt`。把该文件夹交给能操作 StandRig 的 AI，按提示词做带物理的高可动模型。
4. 做好的模型要进 OBS、跟摄像头和口型：用 [StandRig Connect](https://github.com/sayaka-aiart/standrig-connect)。它负责脸部追踪、五元音口型、Spout2。本体的浏览器舞台仍然可以单独用。

StandRig 不读 Live2D 的 `.motion3.json`。表情可以和 Live2D `.exp3.json` 互导。摄像头追踪不在 StandRig 本体里，在 Connect。
