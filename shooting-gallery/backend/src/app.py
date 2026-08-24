import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


CONNECTIONS_TABLE = os.environ["CONNECTIONS_TABLE"]
STATE_TABLE = os.environ["STATE_TABLE"]
ROOM_ID = os.environ.get("ROOM_ID", "gallery-main")
MAX_IMPACTS = int(os.environ.get("MAX_IMPACTS", "500"))
CONNECTION_TTL_SECONDS = int(os.environ.get("CONNECTION_TTL_SECONDS", "7200"))

dynamodb = boto3.resource("dynamodb")
connections = dynamodb.Table(CONNECTIONS_TABLE)
states = dynamodb.Table(STATE_TABLE)


def response(status_code=200, body=None):
    return {
        "statusCode": status_code,
        "body": json.dumps(body or {}, separators=(",", ":")),
    }


def decimal_to_native(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [decimal_to_native(item) for item in value]
    if isinstance(value, dict):
        return {key: decimal_to_native(item) for key, item in value.items()}
    return value


def empty_state():
    return {
        "room_id": ROOM_ID,
        "generation": 1,
        "sequence": 0,
        "shots": 0,
        "directs": 0,
        "total_error": Decimal("0"),
        "impacts": [],
    }


def get_state():
    item = states.get_item(Key={"room_id": ROOM_ID}, ConsistentRead=True).get("Item")
    if item:
        return item
    state = empty_state()
    try:
        states.put_item(Item=state, ConditionExpression="attribute_not_exists(room_id)")
        return state
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return states.get_item(Key={"room_id": ROOM_ID}, ConsistentRead=True)["Item"]


def state_payload(state, include_impacts=True):
    native = decimal_to_native(state)
    shots = native.get("shots", 0)
    payload = {
        "roomId": ROOM_ID,
        "generation": native.get("generation", 1),
        "sequence": native.get("sequence", 0),
        "shots": shots,
        "directs": native.get("directs", 0),
        "totalError": native.get("total_error", 0),
        "meanError": round(native.get("total_error", 0) / shots) if shots else 0,
    }
    if include_impacts:
        payload["impacts"] = native.get("impacts", [])
    return payload


def parse_body(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, json.JSONDecodeError):
        raise ValueError("Message body must be valid JSON")
    if not isinstance(body, dict):
        raise ValueError("Message body must be a JSON object")
    return body


def bounded_number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < low or value > high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return float(value)


def short_string(value, name, maximum=128):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")
    return value


def validate_impact(message):
    supplied = message.get("impact") or {}
    band = supplied.get("band")
    if band not in {"direct", "mild", "severe"}:
        raise ValueError("impact.band must be direct, mild, or severe")
    return {
        "id": short_string(supplied.get("id"), "impact.id"),
        "shooterId": short_string(supplied.get("shooterId"), "impact.shooterId"),
        "band": band,
        "x": Decimal(str(bounded_number(supplied.get("x"), "impact.x", -0.5, 1.5))),
        "y": Decimal(str(bounded_number(supplied.get("y"), "impact.y", -0.5, 1.5))),
        "error": Decimal(str(bounded_number(supplied.get("error"), "impact.error", 0, 300))),
        "radius": Decimal(str(bounded_number(supplied.get("radius"), "impact.radius", 0.001, 0.1))),
    }


def validate_pointer(message):
    supplied = message.get("pointer") or {}
    pointer_id = supplied.get("id")
    if pointer_id not in {"one", "two", "three", "four", "five"}:
        raise ValueError("pointer.id must identify one of the five pointers")
    painting = supplied.get("painting")
    if not isinstance(painting, bool):
        raise ValueError("pointer.painting must be a boolean")
    return {
        "id": pointer_id,
        "eventId": short_string(supplied.get("eventId"), "pointer.eventId"),
        "x": bounded_number(supplied.get("x"), "pointer.x", 0, 1),
        "y": bounded_number(supplied.get("y"), "pointer.y", 0, 1),
        "painting": painting,
    }


def management_client(event):
    context = event["requestContext"]
    endpoint = f"https://{context['domainName']}/{context['stage']}"
    return boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)


def send(client, connection_id, payload):
    client.post_to_connection(
        ConnectionId=connection_id,
        Data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )


def send_safely(client, connection_id, payload):
    try:
        send(client, connection_id, payload)
        return True
    except client.exceptions.GoneException:
        connections.delete_item(Key={"room_id": ROOM_ID, "connection_id": connection_id})
        return False


def broadcast(event, payload):
    result = connections.query(
        KeyConditionExpression=Key("room_id").eq(ROOM_ID),
        ProjectionExpression="connection_id",
        ConsistentRead=True,
    )
    ids = [item["connection_id"] for item in result.get("Items", [])]
    while "LastEvaluatedKey" in result:
        result = connections.query(
            KeyConditionExpression=Key("room_id").eq(ROOM_ID),
            ProjectionExpression="connection_id",
            ConsistentRead=True,
            ExclusiveStartKey=result["LastEvaluatedKey"],
        )
        ids.extend(item["connection_id"] for item in result.get("Items", []))

    client = management_client(event)
    with ThreadPoolExecutor(max_workers=min(10, max(1, len(ids)))) as executor:
        list(executor.map(lambda connection_id: send_safely(client, connection_id, payload), ids))


def handle_connect(_event):
    return response()


def handle_disconnect(event):
    connection_id = event["requestContext"]["connectionId"]
    connections.delete_item(Key={"room_id": ROOM_ID, "connection_id": connection_id})
    return response()


def handle_join(event):
    connection_id = event["requestContext"]["connectionId"]
    connections.put_item(
        Item={
            "connection_id": connection_id,
            "room_id": ROOM_ID,
            "expires_at": int(time.time()) + CONNECTION_TTL_SECONDS,
        }
    )
    payload = {"type": "snapshot", "state": state_payload(get_state())}
    send(management_client(event), connection_id, payload)
    return response()


def handle_fire(event, message):
    current = get_state()
    if len(current.get("impacts", [])) >= MAX_IMPACTS:
        send(
            management_client(event),
            event["requestContext"]["connectionId"],
            {"type": "error", "message": "Target is full; replace it before firing again."},
        )
        return response()

    impact = validate_impact(message)
    direct_increment = 1 if impact["band"] == "direct" else 0
    updated = states.update_item(
        Key={"room_id": ROOM_ID},
        UpdateExpression=(
            "SET #impacts = list_append(if_not_exists(#impacts, :empty), :impact), "
            "#sequence = if_not_exists(#sequence, :zero) + :one, "
            "#shots = if_not_exists(#shots, :zero) + :one, "
            "#directs = if_not_exists(#directs, :zero) + :direct, "
            "#total_error = if_not_exists(#total_error, :zero) + :error"
        ),
        ExpressionAttributeNames={
            "#impacts": "impacts",
            "#sequence": "sequence",
            "#shots": "shots",
            "#directs": "directs",
            "#total_error": "total_error",
        },
        ExpressionAttributeValues={
            ":empty": [],
            ":impact": [impact],
            ":zero": 0,
            ":one": 1,
            ":direct": direct_increment,
            ":error": impact["error"],
        },
        ReturnValues="ALL_NEW",
    )["Attributes"]

    payload = {
        "type": "impact",
        "impact": decimal_to_native(impact),
        "state": state_payload(updated, include_impacts=False),
    }
    broadcast(event, payload)
    return response()


def handle_replace(event):
    current = get_state()
    replacement = empty_state()
    replacement["generation"] = int(current.get("generation", 1)) + 1
    states.put_item(Item=replacement)
    broadcast(event, {"type": "target_replaced", "state": state_payload(replacement)})
    return response()


def handle_pointer(event, message):
    broadcast(event, {"type": "pointer", "pointer": validate_pointer(message)})
    return response()


def handle_clear_paint(event):
    broadcast(event, {"type": "paint_cleared"})
    return response()


def handle_default(event):
    connection_id = event["requestContext"]["connectionId"]
    send(
        management_client(event),
        connection_id,
        {"type": "error", "message": "Unknown action"},
    )
    return response()


def lambda_handler(event, _context):
    route = event.get("requestContext", {}).get("routeKey")
    try:
        if route == "$connect":
            return handle_connect(event)
        if route == "$disconnect":
            return handle_disconnect(event)

        message = parse_body(event)
        if route == "join":
            return handle_join(event)
        if route == "fire":
            return handle_fire(event, message)
        if route == "pointer":
            return handle_pointer(event, message)
        if route == "clearPaint":
            return handle_clear_paint(event)
        if route == "replaceTarget":
            return handle_replace(event)
        return handle_default(event)
    except ValueError as error:
        connection_id = event.get("requestContext", {}).get("connectionId")
        if connection_id:
            send_safely(
                management_client(event),
                connection_id,
                {"type": "error", "message": str(error)},
            )
        return response(400, {"error": str(error)})
