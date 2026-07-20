# Code-Fix Deliverable — cli-anything-zigbee2mqtt

## Confirmed Finding Fixed
**File:** `cli_anything/zigbee2mqtt/core/mqtt_client.py`  
**Location:** `BridgeClient._on_message`, line 169  
**Finding:** `except Exception: pass` silently swallowed all subscriber callback exceptions  
**Fix:** Changed `pass` → `raise` so exceptions propagate to the caller

```python
# Before
except Exception:
    pass

# After
except Exception:
    raise
```

## Regression Test Added / Fixed
**File:** `cli_anything/zigbee2mqtt/tests/test_core.py`  
**Test:** `TestBridgeClient::test_subscriber_exception_propagates`  
**Three bugs corrected:**
1. `MQTTMessage(topic="z2m/...")` — passed `str`; paho API expects `bytes` → `topic=b"z2m/..."`
2. `client.client.on_message(...)` was called without catching the exception → wrapped in `with pytest.raises(RuntimeError, match=exc_msg)`
3. Payload assertion used dict literal `{"key": "val"}` but callback receives a string → `'`{"key": "val"}'`

## Verification
```
$ python -m pytest -q
.......................................................... [100%]
58 passed in 0.09s
```

## Files Changed
- `cli_anything/zigbee2mqtt/core/mqtt_client.py` — 1 line (+1/-1)
- `cli_anything/zigbee2mqtt/tests/test_core.py` — 28 lines added
