"""Common boundary role descriptions and two small host codec primitives.

No source dependency is deleted, no wire capability is minted, and no selector
input is changed. Execution resources are described, not serialized or moved.
"""
import copy


def describe_boundary(description, *, input_roles, ambient_resources, output_decoders, evidence):
    scope = description['scope']
    inputs, outputs = scope['interface_inputs'], scope['interface_outputs']
    if set(input_roles) != set(inputs):
        raise ValueError('Every source input needs exactly one boundary role')
    for name, spec in input_roles.items():
        if spec.get('type') != inputs[name]:
            raise ValueError('Boundary input type mismatch: '+name)
        role = spec.get('role')
        if role == 'parameter':
            if not spec.get('binding'):
                raise ValueError('Parameter binding missing: '+name)
        elif role in ['resident_resource', 'host_configuration']:
            if not spec.get('owner') or not spec.get('lifetime'):
                raise ValueError('Resource/configuration lifecycle missing: '+name)
            if spec.get('serialized') is not False:
                raise ValueError('Resource/configuration cannot silently become a wire value: '+name)
        else:
            raise ValueError('Unknown boundary role: '+name)
    for name, spec in ambient_resources.items():
        if name in inputs or not spec.get('owner') or not spec.get('lifetime') or spec.get('serialized') is not False:
            raise ValueError('Invalid ambient execution resource: '+name)
    if set(output_decoders) != set(outputs):
        raise ValueError('Every externally consumed value needs a decoder description')
    for name, spec in output_decoders.items():
        if spec.get('type') != outputs[name] or not spec.get('transport_shape') or not spec.get('host_decode'):
            raise ValueError('Output representation/decoder missing: '+name)
    return dict(schema='backend-boundary-description/v1',
        input_roles=copy.deepcopy(input_roles), ambient_resources=copy.deepcopy(ambient_resources),
        parameter_values={n:inputs[n] for n,s in input_roles.items() if s['role']=='parameter'},
        retained_environment={n:copy.deepcopy(s) for n,s in input_roles.items() if s['role']!='parameter'},
        output_decoders=copy.deepcopy(output_decoders), evidence=copy.deepcopy(evidence),
        source_dependencies_preserved=True, changes_selector_input=False, authorizes_lowering=False,
        authority='Explicit source-bound role/codec description, not proof of arbitrary environment effects')


def positional_parameters(*values):
    """Keep native driver parameter objects; no numeric/text coercion."""
    return values


def decode_first_field(rows, field, convert):
    """Decode a fully fetched ordered row packet using the original converter.

    Empty list IndexError and conversion exceptions propagate. This primitive
    does not authorize removing getters/constructors, narrowing SQL, LIMIT,
    skipping reads, moving transaction boundaries, or sharing model identities.
    """
    return convert(rows[0][field])
