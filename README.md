# AI 内容鉴别插件 (AI Content Detector)

AstrBot AI 内容鉴别插件，支持文字（腾讯朱雀）和图片（Sightengine）的 AI 生成检测。

## 功能特点

- ✅ 文字检测：使用腾讯 EdgeOne Makers 朱雀模型
- ✅ 图片检测：使用 Sightengine genai 模型
- ✅ 支持引用消息检测、当前消息检测、最近消息检测
- ✅ 智能触发词识别（"是ai吗"/"是不是ai"/"ai生成的吗"等多种问法）
- ✅ 防刷机制：冷却时间 + 每日额度限制
- ✅ 群组白名单支持

## 安装

1. 下载本插件的所有文件
2. 放入 AstrBot 的 `data/plugins/astrbot_plugin_kafu_aidetect/` 目录
3. 重启 AstrBot
4. 在后台管理界面配置 API 密钥

## API 密钥获取

### 腾讯朱雀（文字检测）

1. 访问 [EdgeOne Makers](https://ai-gateway.edgeone.link/)
2. 登录后进入控制台 → Makers → Models → API Key
3. 复制 API Key 填入插件配置的 `zhuque_api_key`
4. 免费额度：每月 50 万 token

### Sightengine（图片检测）

1. 访问 [Sightengine](https://sightengine.com/) 注册账号
2. 登录后在 Dashboard 查看 `api_user` 和 `api_secret`
3. 填入插件配置的对应字段
4. 免费额度：每月 2000 operations（约 400 张图），每天最多 500 operations

## 使用方法

### 群聊中使用

1. **引用消息检测**（推荐）：
   ```
   [引用某条消息] @机器人 是ai吗
   ```

2. **当前消息检测**：
   ```
   @机器人 这段是不是ai写的
   [附上要检测的文字或图片]
   ```

3. **最近消息检测**（可在配置中关闭）：
   ```
   是ai吗
   ```
   机器人会根据最近的群消息判断你指哪条，拿不准会先询问。

### 私聊中使用

发送消息：
```
是ai吗
[附上要检测的文字或图片]
```

### 触发词

插件支持多种问法，例如：
- `是ai吗` / `是不是ai` / `是ai写的吗`
- `ai生成的吗` / `ai画的吗`
- `鉴别一下是不是ai` / `检测ai`
- `这图是ai画的吧` / `ai鉴定`

## 配置说明

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable` | 布尔 | `true` | 是否启用 AI 鉴别 |
| `zhuque_api_key` | 字符串 | `""` | 腾讯 EdgeOne Makers 的 API Key（文字检测） |
| `sightengine_api_user` | 字符串 | `""` | Sightengine 的 api_user（图片检测） |
| `sightengine_api_secret` | 字符串 | `""` | Sightengine 的 api_secret（图片检测） |
| `image_use_proxy` | 布尔 | `false` | 图片检测是否走容器代理（直连失败时可尝试开启） |
| `bare_trigger` | 布尔 | `true` | 群里不 @ 也能用（只认整条消息就是"是ai吗"这类短句） |
| `cooldown_seconds` | 整数 | `20` | 同一人两次鉴别的间隔（秒） |
| `daily_text_limit` | 整数 | `200` | 全局每天文字鉴别次数上限 |
| `daily_image_limit` | 整数 | `13` | 全局每天图片鉴别次数上限（每张图扣 5 operations） |
| `max_images` | 整数 | `2` | 一次最多鉴别几张图 |
| `min_text_chars` | 整数 | `10` | 少于这么多字不调用接口 |
| `short_text_chars` | 整数 | `50` | 少于这么多字会提醒"短文本容易误判" |
| `recent_size` | 整数 | `15` | 每个群在内存里记住最近多少条消息（30 分钟后过期） |
| `allow_groups` | 列表 | `[]` | 只在这些群启用（留空 = 全部群） |

## 工作原理

1. **请求识别**：插件识别用户是否在询问"是不是 AI 生成"
2. **目标定位**：
   - 有引用 → 检测被引用的那条消息
   - 无引用 → 根据上下文判断（可能询问用户）
3. **内容提取**：
   - 文字：从消息文本中提取
   - 图片：从消息中的图片组件提取
4. **API 调用**：
   - 文字：调用腾讯朱雀 API
   - 图片：调用 Sightengine API
5. **结果返回**：机器人按人设口吻回复检测结果

## 已知限制

- **朱雀文字检测**：
  - 短文本（<50 字）容易误判
  - 对口语、翻译腔、格式化文本不太准确
  - 少于 10 字的文本会跳过检测
- **Sightengine 图片检测**：
  - 服务器在海外，可能需要代理
  - QQ 图片地址可能失效，建议引用原消息检测
  - 每张图片消耗 5 operations，免费额度有限

## 数据隐私

- 最近消息只保存在内存中，30 分钟后自动过期
- 日志只记录字数、结论和概率，不记录正文内容
- 配额文件（`quota.json`）只记录日期和次数统计

## 兼容性

- AstrBot >= 4.27.0

## 许可证

MIT License

## 反馈与贡献

如有问题或建议，欢迎提 Issue 或 PR。

## 相关链接

- [腾讯 EdgeOne Makers](https://ai-gateway.edgeone.link/)
- [Sightengine](https://sightengine.com/)
