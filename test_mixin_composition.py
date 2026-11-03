"""Static ADR-0028 guard for dependencies of the actual Agent composition.

No Agent is instantiated: the guard reads the complete MRO's class sources.
It checks declared attributes, not whether every control-flow path initializes
them. Optional getattr defaults remain optional; required literal getattr reads
have the same contract as direct self.<name> reads.
"""
import ast
import inspect
import textwrap
import unittest

import tg_ingest_agent as entry


def _is_self(node):
    return isinstance(node, ast.Name) and node.id == "self"


class _SelfContract(ast.NodeVisitor):
    def __init__(self):
        self.required = set()
        self.declared = set()

    def visit_ClassDef(self, node):
        # A nested class has its own receiver, unrelated to its enclosing mixin.
        return

    def visit_Attribute(self, node):
        if _is_self(node.value):
            if isinstance(node.ctx, ast.Store):
                self.declared.add(node.attr)
            elif isinstance(node.ctx, (ast.Load, ast.Del)):
                self.required.add(node.attr)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        # A bare annotation does not create an instance value.
        if node.value is not None:
            self.visit(node.target)
            self.visit(node.value)

    def visit_AugAssign(self, node):
        # += reads an existing attribute; it cannot declare a missing provider.
        if isinstance(node.target, ast.Attribute) and _is_self(node.target.value):
            self.required.add(node.target.attr)
        else:
            self.visit(node.target)
        self.visit(node.value)

    def visit_Call(self, node):
        if (isinstance(node.func, ast.Name) and len(node.args) >= 2
                and _is_self(node.args[0])
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            name = node.args[1].value
            if node.func.id == "setattr" and len(node.args) == 3:
                self.declared.add(name)
            elif node.func.id == "getattr" and len(node.args) == 2:
                self.required.add(name)
            elif node.func.id == "delattr":
                self.required.add(name)
        self.generic_visit(node)


def _class_contract(cls):
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    source = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    visitor = _SelfContract()
    for statement in source.body:
        visitor.visit(statement)
    return visitor.required, set(vars(cls)) | visitor.declared


def missing_self_dependencies(composed):
    """Find direct/literal required reads without any provider in this MRO."""
    classes = [cls for cls in composed.__mro__ if cls is not object]
    contracts = {cls: _class_contract(cls) for cls in classes}
    declared = set().union(*(contract[1] for contract in contracts.values()))
    # Agent's own initialization supplies instance fields to all mixins. Its
    # methods are providers too; only inherited mixin reads are this ADR's gate.
    return {cls.__qualname__: sorted(required - declared)
            for cls, (required, _) in contracts.items()
            if cls is not composed and required - declared}


class _Providers:
    label = "synthetic"

    def initialize(self):
        self.cfg, self.conn = None, None
        setattr(self, "delivered", False)

    def reply(self, text):
        return text


class _UsesProviders:
    def read(self):
        return self.reply((self.cfg, self.conn, self.label, self.delivered))


class _Composed(_UsesProviders, _Providers):
    pass


class _UsesMissingMethod:
    def read(self):
        return self.replly(self.cfg)


class _MissingMethod(_UsesMissingMethod, _Providers):
    pass


class _UsesMissingField:
    def read(self):
        return self.undeclared_field


class _MissingField(_UsesMissingField, _Providers):
    pass


class _UsesOptionalFields:
    def read(self):
        return (getattr(self, "optional_field", None),
                hasattr(self, "optional_hook"))


class _OptionalFields(_UsesOptionalFields):
    pass


class _UsesRequiredDynamicField:
    def read(self):
        return getattr(self, "required_field")


class _RequiredDynamicField(_UsesRequiredDynamicField):
    pass


class _OnlyAnnotatesField:
    def initialize(self):
        self.undeclared_field: str


class _AnnotationCannotProvide(_UsesMissingField, _OnlyAnnotatesField):
    pass


class _OnlyIncrementsField:
    def increment(self):
        self.undeclared_field += 1


class _IncrementCannotProvide(_UsesMissingField, _OnlyIncrementsField):
    pass


class MixinCompositionTests(unittest.TestCase):
    def test_actual_agent_mixins_have_declared_dependencies(self):
        self.assertEqual(missing_self_dependencies(entry.Agent), {})

    def test_inherited_methods_class_values_and_instance_fields_supply_contract(self):
        self.assertEqual(missing_self_dependencies(_Composed), {})

    def test_missing_cross_mixin_method_is_reported(self):
        self.assertEqual(missing_self_dependencies(_MissingMethod), {
            "_UsesMissingMethod": ["replly"]})

    def test_missing_instance_field_is_reported(self):
        self.assertEqual(missing_self_dependencies(_MissingField), {
            "_UsesMissingField": ["undeclared_field"]})

    def test_optional_defaults_and_presence_probes_need_no_provider(self):
        self.assertEqual(missing_self_dependencies(_OptionalFields), {})

    def test_required_literal_getattr_needs_a_provider(self):
        self.assertEqual(missing_self_dependencies(_RequiredDynamicField), {
            "_UsesRequiredDynamicField": ["required_field"]})

    def test_bare_annotation_cannot_hide_a_missing_instance_provider(self):
        self.assertEqual(missing_self_dependencies(_AnnotationCannotProvide), {
            "_UsesMissingField": ["undeclared_field"]})

    def test_augmented_assignment_cannot_hide_a_missing_instance_provider(self):
        self.assertEqual(missing_self_dependencies(_IncrementCannotProvide), {
            "_UsesMissingField": ["undeclared_field"],
            "_OnlyIncrementsField": ["undeclared_field"]})


if __name__ == "__main__":
    unittest.main()
