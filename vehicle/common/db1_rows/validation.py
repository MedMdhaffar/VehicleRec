"""Caller-loaded P1 schemas; construction itself has no filesystem/network I/O."""
import json
from jsonschema import Draft202012Validator, FormatChecker
from contracts.db1.reference import parse_timestamp
from contracts.db1.row_policies import validate_snapshot_semantics


class ContractValidation:
    def __init__(self, row_schema, diagnostic_schema):
        # Own schema copies and prohibit external refs (no resolver network access).
        row_schema, diagnostic_schema = json.loads(json.dumps([row_schema, diagnostic_schema]))
        def check_refs(node):
            if isinstance(node, dict):
                for keyword in ('$ref', '$dynamicRef'):
                    if keyword in node and not node[keyword].startswith('#/'):
                        raise ValueError('only internal schema references are supported')
                for child in node.values():
                    check_refs(child)
            elif isinstance(node, list):
                for child in node:
                    check_refs(child)
        for schema in (row_schema, diagnostic_schema):
            check_refs(schema)
            Draft202012Validator.check_schema(schema)
        if row_schema.get('title') != 'WAYCON DB1 vehicle row snapshot v0' or diagnostic_schema.get('title') != 'WAYCON DB1 minimal diagnostic sidecar v1':
            raise ValueError('expected DB1 P1 row v0 and diagnostics v1 schemas')
        formats = FormatChecker()
        @formats.checks('date-time', raises=ValueError)
        def supported_timestamp(value):
            if isinstance(value, str):
                parse_timestamp(value)
            return True
        self._row = Draft202012Validator(row_schema, format_checker=formats)
        self._diagnostic = Draft202012Validator(diagnostic_schema, format_checker=formats)
        self._delivery = {kind: Draft202012Validator(
            {'$ref': f'#/$defs/{name}_delivery', '$defs': row_schema['$defs']}, format_checker=formats)
            for kind, name in (('fact_vehicle', 'vehicle'), ('fact_vehicle_colour', 'colour'), ('fact_plate_read', 'plate'))}

    def delivery(self, kind, payload):
        self._delivery[kind].validate(payload)

    def snapshot(self, snapshot, lookup):
        self._row.validate(snapshot)
        validate_snapshot_semantics(snapshot, lookup)

    def diagnostic(self, payload):
        self._diagnostic.validate(payload)
