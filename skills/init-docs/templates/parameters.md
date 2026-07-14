# Parameters Reference

Single source of truth for all configurable parameters.

> **Rule**: If a parameter is documented anywhere else, it should reference this file.

## Runtime Parameters

| Parameter | Default | Description | Valid Range |
|-----------|---------|-------------|-------------|
| {{PARAM_1}} | {{DEFAULT}} | {{DESCRIPTION}} | {{RANGE}} |

## Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `{{VAR_1}}` | {{YES/NO}} | {{DESCRIPTION}} | `{{EXAMPLE}}` |

## Configuration Files

### {{CONFIG_FILE_1}}
```{{FORMAT}}
{{EXAMPLE_CONTENT}}
```

| Field | Type | Description |
|-------|------|-------------|
| {{FIELD_1}} | {{TYPE}} | {{DESCRIPTION}} |

## Constants

| Constant | Value | Source | Notes |
|----------|-------|--------|-------|
| {{CONST_1}} | {{VALUE}} | {{SOURCE}} | {{NOTES}} |

## Computed Values

| Value | Formula | Depends On |
|-------|---------|------------|
| {{COMPUTED_1}} | {{FORMULA}} | {{DEPENDENCIES}} |

## Version History

| Date | Parameter | Change | Reason |
|------|-----------|--------|--------|
| {{DATE}} | {{PARAM}} | {{OLD}} → {{NEW}} | {{REASON}} |
