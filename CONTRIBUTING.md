# Contributing

## Writing a Custom Adapter

This repo currently has a simple adapter hook in `vibe_debug/adapters.py`.
There is not a registry API yet, so the current pattern is:

1. write an `instrument_<library>()` function
2. return `True` if the library was found and patched
3. return `False` if the library is not installed
4. call that function from `instrument_all_adapters()`

Example:

```python
import functools

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


def instrument_stripe() -> bool:
    try:
        import stripe
    except ImportError:
        return False

    tracer = trace.get_tracer("vibe_debug.adapters.stripe")
    original_create = stripe.PaymentIntent.create

    @functools.wraps(original_create)
    def patched_create(*args, **kwargs):
        with tracer.start_as_current_span("stripe.payment_intent.create") as span:
            span.set_attribute("vibe.type", "payment")
            span.set_attribute("payment.amount", kwargs.get("amount", 0))
            span.set_attribute("payment.currency", kwargs.get("currency", ""))
            try:
                result = original_create(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))
                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    stripe.PaymentIntent.create = patched_create
    return True
```

## Adapter Rules

- Return `True` if the target library was found and patched successfully.
- Return `False` if the target library is not installed.
- Always use `functools.wraps` so signatures and metadata stay intact.
- Set `vibe.type` so the span is easy to categorize later.
- Always record exceptions and then re-raise them.
- Keep patches small and reversible when possible.

## How to Test an Adapter

At minimum, make sure the adapter can fail safely when the package is missing:

```python
assert instrument_stripe() in (True, False)
```

If the library is installed locally, also verify:

- the patched call still returns the original result
- errors still propagate to the caller
- the span shows up in Phoenix with the expected attributes
