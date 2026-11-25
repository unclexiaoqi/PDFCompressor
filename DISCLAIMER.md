# 免责声明 / Disclaimer

> 在使用 PDF 压缩器（以下简称"本软件"）前，请仔细阅读并充分理解本声明。
> **继续下载、安装或使用本软件，即表示你已阅读、理解并同意以下全部内容；如不同意，请停止使用并删除本软件。**
>
> 本软件为个人开源项目，**仅用于学习、研究与技术交流**，不是商业产品。
> 本文不是法律意见；若用于商业场景或涉及纠纷，请咨询专业律师。
> 最后更新：2026-11-25

## 1 软件性质

本软件是一款基于 PyMuPDF（MuPDF）、pikepdf（qpdf）、Pillow、numpy、fontTools、tkinterdnd2 等开源组件构建的
本地 PDF 压缩工具，仅提供**本机**的图像降采样与重编码、字体子集化、PDF 对象流与流的重新压缩、
按需删除元数据/缩略图/附件/书签/注释/结构树、线性化等文档优化功能。

本软件自身不提供任何在线压缩、文件中转、云端存储、账号体系或代客处理服务，也不与任何此类服务的提供者存在
合作、代理或担保关系。压缩全部在你的本机完成，文件不会上传到任何服务器。

本软件采用 PDF 文档优化中常见的做法：图像缩减像素采样与重新压缩、字体子集化、放弃无用对象、
对象流压缩等；不保证任何特定压缩比例或画质表现。

## 2 用途说明（仅限学习与交流）

本项目由个人开发者以学习、研究和技术交流为目的开源，**仅供学习、研究与技术交流使用**，不作为商业产品提供，
也不附带任何付费服务、服务水平承诺（SLA）或技术支持义务。

任何人基于本软件开展的商业活动，包括付费分发、捆绑销售、作为商业服务或交付成果的组成部分，均与本软件的
开发者无关，开发者不为其提供背书、支持或担保。

## 3 用户责任

你应自行确认使用本软件在你所在国家或地区是合法的，并对使用本软件的全部行为及其后果独立承担法律责任，
包括但不限于压缩、复制、传播或交付他人文件所涉及的著作权、保密义务、数据安全与行业合规问题。

你导入的文件、你选择的压缩预设与参数、以及输出文件的后续使用方式，均由你自行选择与判断；源文件是否合法、
内容是否涉密或受保护、压缩结果是否适用于你的用途，均由你负责。请只处理你**拥有合法权利**的文件。

**压缩属于有损处理**：图像会被降采样并重新编码，文字与矢量的渲染效果、颜色、元数据、书签、注释、表单、
附件等内容可能被移除或改变。请在压缩前**备份原件**，对合同、图纸、投标文件、印刷稿等重要材料务必人工复核
后再使用输出文件。

本软件不破解加密文档、不绕过 DRM 或任何授权限制；遇到需要打开密码的文档会直接拒绝处理，
遇到受权限保护的文档默认跳过（需在设置中显式允许解密后才会处理，且不解除原文档的授权限制）。

## 4 第三方内容

本软件使用 **PyMuPDF / MuPDF**（解析与字体子集化）、**pikepdf / qpdf**（PDF 结构读写与对象流压缩）、
**Pillow**（图像解码与重编码）、**numpy**（像素运算）、**fontTools**（字体子集化）、
**tkinterdnd2 / Tcl / Tk**（图形界面与拖拽）等第三方开源组件；这些组件各自遵循其许可协议
（包括但不限于 AGPL-3.0、MPL-2.0、MIT、BSD），其行为、输出与安全性由各自所有者负责，
开发者无法也未曾对其合法性、准确性、安全性和可用性进行审查或担保。

因使用第三方软件或第三方组件导致的数据泄露、文件损坏、画质异常、业务中断或其他任何损失，
由你与相应提供方自行解决，开发者不承担任何责任。

## 5 无担保声明

本软件按"现状"（AS IS）和"可用"（AS AVAILABLE）的原则提供，不附带任何明示或默示的担保，
包括但不限于适销性、特定用途适用性、不侵权，以及持续可用、无错误、无病毒或无安全漏洞的担保。

开发者不保证本软件能满足你的需求，不保证压缩后的文件在所有阅读器、打印机或平台上显示一致，
也不保证压缩比例、画质损失、页数、版面、字体嵌入状态与原件完全符合预期，
更不保证本软件的运行不会中断或不会出现错误。

本软件内置了体积护栏、单图 PSNR 画质闸门与压缩后渲染自检，但这些只是启发式保护措施，
**不能替代你自己对重要文件的核对**。

## 6 责任限制

在适用法律允许的最大范围内，开发者及所有贡献者均不对因使用或无法使用本软件而产生的任何直接、间接、偶然、
特殊、惩罚性或后果性损害承担责任，包括但不限于数据丢失、文件损坏、设备损坏、业务中断、利润损失、
商业秘密泄露、合同无法履行、投标或评审结果受影响，以及由此引起的任何法律纠纷，
即使已被告知发生此类损害的可能性。

上述限制不适用于依法不得排除或限制的责任（例如因故意或重大过失造成的人身损害）。

## 7 数据收集与隐私

本软件不会收集或上传你的文件内容、文件名、文件路径、设备信息或使用记录，也不集成任何数据统计、遥测
或崩溃上报服务；压缩全程在本机完成，程序本身不发起任何网络请求。

本软件会在**输出文件所在目录**生成临时文件（形如 `.<原文件名>.fonts.<随机串>.pdf`、
`.<原文件名>.out.<随机串>.pdf`），正常情况下在结束时自动清理；若进程被强制结束、断电或磁盘写满，
可能残留，可自行删除。设置文件保存在：

- Windows：`%APPDATA%\PDFCompressor\settings.json`
- macOS：`~/Library/Application Support/PDFCompressor/settings.json`

其中包含你上次选择的预设、自定义参数、输出目录、以及免责声明的确认状态。
卸载时可选择一并删除（默认保留），你也可以随时手动删除该目录。

## 8 开源协议

本软件以 **MIT** 协议开源，你可以依该协议运行、研究、修改和分发本软件；分发本软件或其修改版本时，
请保留原有的版权声明与许可声明。

本软件包含的第三方组件分别遵循其各自的开源协议。**特别提示：PyMuPDF 采用 AGPL-3.0**，
若你分发本项目的二进制产物（如 `PDFCompressor.exe`、`.app`、安装包），需自行满足 AGPL 的源码提供义务；
闭源商业使用需向 Artifex 取得商业授权。本项目的 `PDFCompressor.spec` 只打包二进制，不改变上述许可义务。
基于本软件修改或分发的版本所产生的任何问题，与原作者无关。

## 9 商标声明

本软件为个人开源项目，与所使用第三方组件（MuPDF / PyMuPDF、qpdf / pikepdf、Pillow 等）的所有者
无任何隶属、赞助或背书关系。

文中出现的 "MuPDF"、"PyMuPDF"、"Tcl/Tk" 等名称与商标归各自所有者，仅用于说明技术依赖，
不构成任何形式的授权、认可或合作。

## 10 声明变更

本声明可能随版本更新而修订，以每个版本附带的内容为准；继续使用更新后的版本，即视为接受修订后的声明。

本软件在首次启动时会要求完整输入一句确认语句，声明版本更新后会再次询问；
确认状态记录在设置文件的 `disclaimer` 字段中。

## 11 与开源协议的关系

本声明是对软件性质、风险与责任的说明，不是 MIT 协议之外的附加条款，也不限制该协议授予你的运行、复制、
修改和分发等任何权利；两者不一致时，以 MIT 协议为准（第三方组件的许可除外）。

---

## English summary

PDF Compressor is a personal open-source utility **for study and technical exchange only**, not a
commercial product. By downloading, installing or using it you accept this disclaimer. Use it only on
documents you are entitled to process; it does not break encryption, DRM or any access control.
Compression is lossy: images are downsampled and re-encoded, and metadata, bookmarks, annotations,
forms or attachments may be removed — always keep the original and review important output manually.
The software runs entirely on your machine, performs no network requests, and collects no data; it
writes temporary files next to the output PDF and keeps its settings under
`%APPDATA%\PDFCompressor` (Windows) or `~/Library/Application Support/PDFCompressor` (macOS).
It is provided "as is", without warranty of any kind; to the maximum extent permitted by law the
author and contributors are not liable for any direct or indirect damages arising from its use.
The code is MIT licensed; third-party components keep their own licences (PyMuPDF is AGPL-3.0 —
you are responsible for compliance when redistributing binaries). This project is not affiliated
with or endorsed by the owners of the third-party components it uses; all trademarks belong to
their respective owners. This is not legal advice.
