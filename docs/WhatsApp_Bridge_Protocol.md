# WhatsApp Bridge Protocol

This document defines the WebSocket payload contract between CloseClaw and a WhatsApp bridge.

## Transport

- Protocol: WebSocket
- Direction:
  - Bridge -> CloseClaw: inbound events (`message`, `auth_response`, ...)
  - CloseClaw -> Bridge: outbound send requests (`send`)

## Outbound Payload (CloseClaw -> Bridge)

CloseClaw sends:

```json
{
  "type": "send",
  "to": "chat_or_user_id",
  "text": "message text",
  "reply_to_message_id": "optional_source_message_id",
  "quoted_message_id": "optional_source_message_id"
}
```

Field rules:

- `type`: always `send`
- `to`: destination chat/user id
- `text`: plain text content
- `reply_to_message_id`: optional reply target id
- `quoted_message_id`: optional reply target id (alias for bridge compatibility)

Reply semantics:

- When both `reply_to_message_id` and `quoted_message_id` are present, they refer to the same source message id.
- Bridge should treat either field as a reply hint and perform native quoted/reply send if supported by its SDK.
- If bridge does not support reply/quote in current adapter, it should ignore these fields and send a normal message.

## Inbound Payload (Bridge -> CloseClaw)

### 1) User Message Event

```json
{
  "type": "message",
  "id": "wamid_or_bridge_message_id",
  "chat_id": "chat_or_group_id",
  "sender": "user_id_or_jid",
  "content": "user text",
  "isGroup": false
}
```

Required for best routing:

- `id`: source message id (used by CloseClaw for future reply chaining)
- `chat_id`: target chat id for sending responses back
- `sender`: sender id
- `content`: message text

### 2) Auth Response Event

```json
{
  "type": "auth_response",
  "auth_request_id": "req_xxx",
  "approved": true,
  "user_id": "admin_user_id"
}
```

## Compatibility Notes

- CloseClaw keeps backward compatibility: unknown inbound fields are ignored.
- Bridge should also ignore unknown outbound fields to allow protocol evolution.
- Reply fields are optional, but strongly recommended to implement for better conversation UX.

## Minimal Bridge Implementation Checklist

1. Parse outbound `send` payload.
2. If `reply_to_message_id` or `quoted_message_id` exists, call WhatsApp SDK quoted/reply API path.
3. Emit inbound `message` event with stable `id`.
4. Preserve `chat_id` and `sender` mapping for routing and auth checks.
