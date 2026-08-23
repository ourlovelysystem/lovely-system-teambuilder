# Shared Shooting Gallery Backend

This AWS SAM application supplies one authoritative target to every connected browser.

## Deploy

```sh
cd shooting-gallery/backend
sam build
sam deploy --guided --stack-name lovely-system-shared-shooting-gallery --region us-east-1
```

Read the WebSocket URL:

```sh
aws cloudformation describe-stacks \
  --stack-name lovely-system-shared-shooting-gallery \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`WebSocketUrl`].OutputValue' \
  --output text
```

Put that value in `shooting-gallery/config.js`, commit, and allow Amplify to deploy it.

## Protocol

Client messages use `{ "action": ... }` and one of these actions:

- `join`
- `fire`
- `replace_target`

Server messages use `{ "type": ... }` and one of these types:

- `snapshot`
- `impact`
- `target_replaced`
- `error`

The firing client supplies normalized aim coordinates and its viewport dimensions. The server resolves deviation, commits the impact, and broadcasts the same normalized result to every connection.

