# StandRig 交接

这是绘爱さやか（@sayaka_aiart）公开的做法：把已经分好层的 PSD 和下面这句提示词交给 AI，用 [StandRig](https://github.com/sayaka-aiart/StandRig) 做带物理演算的高可动模型。她的记录是大约一个半小时。

推文：https://x.com/sayaka_aiart/status/2116165983090180419

## 提示词

```
こちらのパーツ分けされたpsdファイルを元に
物理演算も入れた高稼働な2Dモデルを
StandRigを使って作成してみて
```

## 怎么用

1. 本仓库的工作室会先把一张立绘分成层，PSD 在 `assets/source/`。
2. 生成形象时，会把 PSD 和提示词抄进 `assets/standrig/工作区/`。
3. 把这个工作区交给支持 StandRig 的 AI 客户端，让它克隆 https://github.com/sayaka-aiart/StandRig 再按提示词建模。
4. 要上摄像头追踪、口型和 OBS：用 [StandRig Connect](https://github.com/sayaka-aiart/standrig-connect)，可把画面用 Spout2 送进 OBS。

当前直播页不依赖这一步。see-through 分层加上网页里的呼吸、眨眼、口型和视差，上传完就可以开播。StandRig 是要更高可动、带物理的时候再做。
