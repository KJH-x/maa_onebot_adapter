# MAA OneBot Adapter

这是一个用于将MAA（MaaAssistantArknights）的报告和通知转发到OneBot平台的适配器。它通过WebSocket接收MAA的报告，并通过HTTP发送OneBot消息。

## 功能

- 接收MAA的运行状态和任务报告。
- 将MAA的报告转发为OneBot消息。
- 支持群聊和私聊消息类型。
- 可配置的对话白名单和消息前缀。

## 安装

1. **克隆仓库**

    ```bash
    git clone https://github.com/KJH-x/maa_onebot_adapter.git
    cd maa_onebot_adapter
    ```

2. **安装依赖**

    使用 `pip` 安装所需的 Python 依赖：

    ```bash
    pip install -r requirements.txt
    ```

## 使用

1. **配置 `config.json`**

    根据您的需求修改 `config.json` 文件。请参考上面的“配置”部分。

2. **启动适配器**

    运行 `notify_host.py` 脚本启动 WebSocket 和 HTTP 服务器：

    ```bash
    python notify_host.py
    ```

## 配置

配置文件 `config.json` 示例：

```json
{
    "reaction_to_sender": [
        {
            "message_type": "group",
            "group_id": 123456789
        },
        {
            "message_type": "private",
            "user_id": 123456789
        }
    ],
    "http_server_info": {
        "http": "http://localhost:5700"
    },
    "client_info": [
        {
            "UA": "OneBot/11",
            "Bearer": {
                "token": "your_onebot_token"
            }
        },
        {
            "UA": "MaaReport/00",
            "Bearer": {
                "token": "your_maa_report_token"
            }
        }
    ],
    "user_map": {
        "MAA_USER_ID": {
            "user_id": 123456789,
            "group_id": 123456789,
            "message_type": "group"
        }
    },
    "log_to": {
        "message_type": "group",
        "group_id": 123456789
    }
}
```

## 许可证

本项目使用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。
