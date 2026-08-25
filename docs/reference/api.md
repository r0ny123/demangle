# Public API

Five functions cover what callers actually do. Everything else on this page is either an
error type you may want to catch or an extension point you may want to use.

```python
import demangle

demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi")
# 'std::vector<int, std::allocator<int>>::push_back(int const&)'
```

::: demangle.api
    options:
      members:
        - demangle
        - demangle_strict
        - parse
        - detect
        - demangle_all
        - languages
        - styles
        - cache_clear
        - cache_stats

## Errors

`demangle()` never raises. `demangle_strict()` and `parse()` raise these, and nothing
else -- a plugin that fails some other way is wrapped, with the original chained.

::: demangle.core.errors
    options:
      members:
        - DemanglingError
        - NotMangledError
        - ParseError
        - TruncatedError
        - LimitExceeded

## Limits

A mangled name is untrusted input. Every bound is configurable per call.

::: demangle.core.limits

## Styles

Where the reference demanglers legitimately disagree, the choice is a style rather than
a silent decision in the parser.

::: demangle.core.style
    options:
      members:
        - Style
        - get_style
        - register_style
        - available_styles
