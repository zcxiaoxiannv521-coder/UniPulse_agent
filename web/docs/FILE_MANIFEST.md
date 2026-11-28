# 本次 C 交付文件索引

> 当前入口：`web/app.py`。遵循仓库分工，所有本次变更均在 `web/`。

## 资料分类

- 当前桌面源码、配置、样例与测试：`web/` 根及相应子目录。
- 最新项目基线参考：`docs/plans/UniPulse_校园智能助手Agent开发方案_V4_企微主入口版.md`（用户补充的 V4 文件）。桌面优先/四页导航为用户后续明确选择，见 README。
- V2、V3 和早期 V4：历史方案参考，不作为全部功能已实现的证明。
- `archive/prototypes/`：4 份历史 HTML，本地脚本模拟交互，不是已接 Agent 的应用。
- `archive/mockups/`：3 张人工排版效果图，不是本次 Streamlit 的运行截图。
- `archive/packages/`：3 份历史独立源码包；当前开发请使用仓库 web 源码。
- 不包含虚拟环境、缓存、真实密钥、数据库或真实私聊。工作区临时脚本/重复备份不作为交付提交。

## 文件与校验

下列 SHA-256 用于核对本次文件内容，索引自身不列入校验。文件更新后应重新生成相应记录。

| 文件（相对 web/） | 字节 | SHA-256 |
|---|---:|---|
| [.env.example](../.env.example) | 392 | `8a1b5361224263c59737da4429cf7b469de576aea58b0887678b13e014d325da` |
| [.gitignore](../.gitignore) | 65 | `670a550b580270ee77b6ca3625f74adb354fd9b8f2febc1f58cda1fd46164e39` |
| [.streamlit/config.toml](../.streamlit/config.toml) | 224 | `4d2d2b6cc91a1a81b41cba78da2f241175afcf80288b48c259a2dd82c101a36e` |
| [API_CONTRACT.md](../API_CONTRACT.md) | 8091 | `559fa50f60d65de6907e62bf5e9d2fcf44bdc58229109255a0d5a162eb104879` |
| [README.md](../README.md) | 4862 | `043a8df1eeb74c2731833d13966eb6c3620e0eaa54184d1835d3060dcfdb7966` |
| [__init__.py](../__init__.py) | 70 | `db6437f23605ee532a96c1d5100a510cdad9ca4f4e653b4eee3a298e6189caa1` |
| [app.py](../app.py) | 24843 | `c9706f6e360f42c2eceb52ea30dc768746c2d891f01e6182d1e3b5c529b2e78b` |
| [archive/mockups/UniPulse_V3_任务与助手区域.png](../archive/mockups/UniPulse_V3_任务与助手区域.png) | 318731 | `253a6562f551e703c4c696061899e0ec664dc6a33b1aa090733408e4b470e494` |
| [archive/mockups/UniPulse_V3_课表与首页上半部分.png](../archive/mockups/UniPulse_V3_课表与首页上半部分.png) | 201965 | `1443c154df4ccd39df4280847e8860f3def398f36f0d6caac3b6df86b229d9b0` |
| [archive/mockups/UniPulse_V3_首页整体效果.png](../archive/mockups/UniPulse_V3_首页整体效果.png) | 592091 | `03b711c8469fda6f6e10dc8f5d2cc0799139b97a7290380e290e9713af911c3c` |
| [archive/packages/UniPulse_成员C_V4对齐版.zip](../archive/packages/UniPulse_成员C_V4对齐版.zip) | 31898 | `c4a0dbef63915c284cc0b4831a901991278c767f6c60eac0b3caa111ec449d1d` |
| [archive/packages/UniPulse_成员C_桌面网页第一版.zip](../archive/packages/UniPulse_成员C_桌面网页第一版.zip) | 35438 | `21b53ec95b00c9ce70c63e5c0850dce976ae5f7de46dedd9c4a149f9e692deeb` |
| [archive/packages/UniPulse_成员C_网页第一版.zip](../archive/packages/UniPulse_成员C_网页第一版.zip) | 23663 | `691c67cd0867c385a4b00edaf68463d8c47de3a25f1ccb42436d07d779ccd21e` |
| [archive/prototypes/unipulse-first-look.html](../archive/prototypes/unipulse-first-look.html) | 31955 | `e916a770524eb3a506684e9348d02a522c342fd6043d6fe367e3f76e83e193f4` |
| [archive/prototypes/unipulse-sysu-v3.html](../archive/prototypes/unipulse-sysu-v3.html) | 54911 | `950142e9ca8fc69cfe4616852dd28b7bf6bf39d042ca44bd8c6ddd6f1ef9793a` |
| [archive/prototypes/unipulse-sysu.html](../archive/prototypes/unipulse-sysu.html) | 42856 | `6c59d2219ce2eb2e6a3d73d3bddb7c204fed57fbe55f6f0825b95fc25f0b83a7` |
| [archive/prototypes/unipulse-with-timetable.html](../archive/prototypes/unipulse-with-timetable.html) | 39171 | `174e1f95749d4803e6e4946edf6a97d3e0012b5fe6dab28979d7629ee382357e` |
| [client.py](../client.py) | 21231 | `e618577cefa37e50a6905cce603dc6b046998ff86d942e8fb55bfe724f7e5e95` |
| [docs/DESKTOP_HANDOFF.md](../docs/DESKTOP_HANDOFF.md) | 9674 | `76b4c7f2001556181d417fa169d511191894a9fb3e650cab5d293f44a2b60293` |
| [docs/INTEGRATION_NOTES.md](../docs/INTEGRATION_NOTES.md) | 3120 | `1c488bc08bdc5b710641fc994060ac78043d6d387293f18c7b7c45b788be1c27` |
| [docs/plans/UniPulse_校园智能助手Agent开发方案_V4_企微主入口版.md](../docs/plans/UniPulse_校园智能助手Agent开发方案_V4_企微主入口版.md) | 25777 | `c2d29a6a581d6d3673214a466ec2841960929acc89f5d6b07b186ebc9c780d8f` |
| [docs/plans/UniPulse_校园智能助手Agent开发方案与AI提示词_V2.md](../docs/plans/UniPulse_校园智能助手Agent开发方案与AI提示词_V2.md) | 37981 | `2651542a5f6e7187d8a71a60457e5b7b352515ac35a47f6418962cd832a4a1ba` |
| [docs/plans/UniPulse_校园智能助手Agent开发方案与AI提示词_V3.md](../docs/plans/UniPulse_校园智能助手Agent开发方案与AI提示词_V3.md) | 52301 | `87f640debdef4ef55fbb8c352a77cca09ea7148d12478faf9ee3faa2f91d72bc` |
| [docs/plans/UniPulse_校园智能助手Agent开发方案与AI提示词_V4_Agent创新版.md](../docs/plans/UniPulse_校园智能助手Agent开发方案与AI提示词_V4_Agent创新版.md) | 57325 | `7ff46acc2cf408275801a8b65867125931a0e4802a8cdd73c56260a335afa8ad` |
| [fixtures/workspace.json](../fixtures/workspace.json) | 5268 | `99fa7980783ad18680accbefae491189c2f2ee2789a5e8c04773fdcbb4b791cd` |
| [requirements.txt](../requirements.txt) | 18 | `0041d04f821378f895d6f281c79c315a81bd619d318d38b1f94195c422681a3c` |
| [tests/test_client.py](../tests/test_client.py) | 6639 | `b40065a083ed6ac2d93f760d8248b4383244a792673f3902a40b83ce5bad2337` |
| [tests/test_ui.py](../tests/test_ui.py) | 9232 | `f5ea93c2ca4dba42a292478130bd7f196218444b10b25172860723ffe42892fa` |
| [tests/test_view_model.py](../tests/test_view_model.py) | 2117 | `071f128299eb97a5a9de1c17ac85584270178a6b4d968a0a3781b6ea8011ca26` |
| [view_model.py](../view_model.py) | 3740 | `03ed65bbdfbce5f4a7ee6d5beff36386674421f26dda2a02d707786fca2d3741` |
| [启动网页.command](../启动网页.command) | 1696 | `17f42f52f63578fa3bade66b9faeba81a1822b42168843cbc2eec6d71e2c40c5` |
