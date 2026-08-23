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

The firing client resolves deviation and renders its crater immediately. It sends the completed normalized impact—not its aim—to the server. The server validates, commits, and broadcasts that result. A stable browser identifier lets each client preserve a durable visual distinction between its own shots and remote shots.
