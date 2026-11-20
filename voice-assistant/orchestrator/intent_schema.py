"""Wire schema for compact intent output; semantic defaults stay in intent._validate."""
from . import config, home_status, nap_status

INTENTS = (
    "set_timer", "timer_query", "timer_adjust", "timer_rename", "timer_cancel",
    "add_items", "set_reminder", "show_todos", "show_shopping", "show_reminders",
    "complete_item",
    "remove_items", "clear_list", "play_music", "music_control", "music_query",
    "sports", "weather", "time_query", "business_hours", "place_search",
    "home_control", "cover_set", "climate_set", "home_status", "nap_status",
    "broadcast", "find_phone", "show_camera", "close_camera",
    "animal_sound", "ask", "show_answer", "unclear", "none",
)

# Optional fields let the model omit unused slots. Null remains valid for an
# explicitly unknown slot; the application normalizes both absence and null.
# Keep the prompt/validator field order: the deployed grammar backend can
# constrain key order, so placing a field earlier can make it unavailable
# after the model emits a later key.
_FIELDS = ("intent", "label", "new_label", "duration_seconds", "sound_theme", "scope", "query",
    "broadcast_target", "phone_owner", "phone_action", "place_modifier",
    "item_text", "list_type", "missing_content", "media_type", "music_action", "music_volume", "sports_action",
    "sports_date", "weather_when", "weather_location", "time_kind", "time_day",
    "hours_when", "camera_target", "cover_target", "cover_position", "climate_target", "climate_setpoint", "animal", "status_scope", "nap_kid")
_PROPERTIES = {name: {"type": ["string", "null"]} for name in _FIELDS}
_PROPERTIES["intent"] = {"type": "string", "enum": list(INTENTS)}
_PROPERTIES.update({name: {"type": ["integer", "null"]}
    for name in ("duration_seconds", "music_volume", "cover_position")})
_PROPERTIES["climate_setpoint"] = {"type": ["number", "null"]}
_PROPERTIES["missing_content"] = {"type": "boolean"}
_PROPERTIES["status_scope"] = {"enum": list(home_status.SCOPES) + [None]}
_PROPERTIES["nap_kid"] = {"enum": list(nap_status.NAP_KIDS) + [None]}
_PROPERTIES["sound_theme"] = {"enum": list(config.SOUND_THEMES) + [None]}
SCHEMA = {"type": "object", "properties": _PROPERTIES,
          "required": ["intent"], "additionalProperties": False}
RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "household_intent", "strict": True, "schema": SCHEMA}}
