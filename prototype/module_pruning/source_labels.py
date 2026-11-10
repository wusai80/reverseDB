"""Source-bound mutation labels, distinct from model predictions/full-app labels."""
import ast
import copy
import dataclasses
import decimal
import hashlib
import io
import itertools
import sys
import types
import zipfile
from pathlib import Path


def fingerprint(node):
    return hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()


def named(nodes, kind, name):
    found = [n for n in nodes if isinstance(n,kind) and n.name==name]
    if len(found)!=1:
        raise ValueError("Expected exactly one source definition: "+name)
    return found[0]


def build_mutant(source, project, candidate):
    original = ast.parse(source)
    changed = copy.deepcopy(original)
    if project=="paperless-ngx":
        name = {"formatted":"_formatted_filepath","plain":"_filename_only"}[candidate]
        cls = named(changed.body,ast.ClassDef,"BulkArchiveStrategy")
        region = named(cls.body,ast.FunctionDef,name)
        cls.body.remove(region)
        references = [n for n in ast.walk(original) if isinstance(n,ast.Attribute) and n.attr==name]
        constructor = named(named(original.body,ast.ClassDef,"BulkArchiveStrategy").body,ast.FunctionDef,"__init__")
        selector = [n for n in constructor.body if isinstance(n,ast.If) and ast.unparse(n.test)=="follow_formatting"]
        if len(selector)!=1 or len(references)!=1:
            raise ValueError("Strategy binding no longer matches bounded proof")
        bindings = []
        for arm in [selector[0].body,selector[0].orelse]:
            if len(arm)!=1 or not isinstance(arm[0],(ast.Assign,ast.AnnAssign)):
                raise ValueError("Unexpected strategy binding")
            target = arm[0].target if isinstance(arm[0],ast.AnnAssign) else arm[0].targets[0]
            if ast.unparse(target)!="self.make_unique_filename":
                raise ValueError("Unexpected strategy consumer")
            bindings.append(ast.unparse(arm[0].value))
        if bindings!=["self._formatted_filepath","self._filename_only"]:
            raise ValueError("Unknown constructor strategy")
        proof = dict(rule="unselected_constructor_binding",binding_true=bindings[0],binding_false=bindings[1],
                     deleted_binding="self."+name,only_reference_line=references[0].lineno)
        compile_original,compile_mutant = original,changed
    elif project=="saleor":
        flag = {"price":"custom_price_to_update","reason":"custom_price_reason_to_update"}[candidate]
        function = named(changed.body,ast.FunctionDef,"_append_line_to_update")
        matches = [n for n in function.body if isinstance(n,ast.If) and ast.unparse(n.test)=="line_data."+flag]
        if len(matches)!=1 or matches[0].orelse:
            raise ValueError("Expected one isolated guarded price region")
        region = matches[0]
        function.body.remove(region)
        proof = dict(rule="false_dataclass_guard",field=flag,guard=ast.unparse(region.test))
        old_function = named(original.body,ast.FunctionDef,"_append_line_to_update")
        future = ast.parse("from __future__ import annotations").body
        compile_original = ast.Module(body=future+[old_function],type_ignores=[])
        compile_mutant = ast.Module(body=copy.deepcopy(future)+[function],type_ignores=[])
    else:
        raise ValueError("Unknown source project")
    return dict(original_tree=compile_original,mutant_tree=compile_mutant,
                original_source=ast.unparse(compile_original)+"\n",mutant_source=ast.unparse(compile_mutant)+"\n",
                region=dict(start=region.lineno,end=region.end_lineno,ast_sha256=fingerprint(region),syntax=ast.unparse(region)),
                proof=proof)


def proof_for_contract(case, mutation, dto_source=None):
    fact = mutation["proof"]
    if case["project"]=="paperless-ngx":
        mode = case["follow_formatting"]
        if type(mode) is not bool:
            raise ValueError("Naming capability option must be a Boolean")
        selected = fact["binding_true"] if mode else fact["binding_false"]
        valid = selected!=fact["deleted_binding"]
        return dict(established=valid,rule=fact["rule"],selected_binding=selected,**{"source_facts":fact},
                    scope="Only constructor/add_document roots and fixed naming policy, no reflection or direct private-method consumers")
    cls = named(ast.parse(dto_source).body,ast.ClassDef,"CheckoutLineData")
    if [ast.unparse(d) for d in cls.decorator_list]!=["dataclass"] or any(not isinstance(n,ast.AnnAssign) for n in cls.body):
        raise ValueError("Dataclass has behavior outside plain-field proof")
    flag = [n for n in cls.body if isinstance(n.target,ast.Name) and n.target.id==fact["field"]]
    if len(flag)!=1 or ast.unparse(flag[0].annotation)!="bool" or not isinstance(flag[0].value,ast.Constant) or flag[0].value.value is not False:
        raise ValueError("Guard field not an ordinary Boolean dataclass field")
    return dict(established=case["contract"]=="disabled",rule=fact["rule"],source_facts=fact,
                scope="Exact ordinary dataclass, both price flags always False under disabled contract; other values unrestricted by sample set")


def decide_label(comparisons, proof):
    witnesses = [p for p in comparisons if not p["equal"] and p["original"]["error"] is None]
    if witnesses:
        return dict(label=1,label_basis="executed_within_contract_counterexample",counterexample=witnesses[0])
    if comparisons and proof.get("established") is True and all(p["equal"] for p in comparisons):
        return dict(label=0,label_basis="conditional_source_unreachability_argument_plus_regression",counterexample=None)
    return dict(label=None,label_basis="unresolved_not_inferred_from_test_pass",counterexample=None)


def execute_tree(tree, name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(ast.fix_missing_locations(tree),name,"exec"),module.__dict__)
        return module.__dict__
    finally:
        sys.modules.pop(name,None)


def zip_scenarios():
    return [dict(documents=docs,strategy=strategy,compression=compression)
            for docs,strategy,compression in itertools.product(
                ["empty","single","collision","archive_fallback"],
                ["OriginalsOnlyStrategy","ArchiveOnlyStrategy","OriginalAndArchiveStrategy"],
                ["stored","deflated"])]


def zip_observation(tree, case, state, directory):
    env = execute_tree(tree,"source_zip_boundary")
    specs = {"empty":[],"single":[("alpha",False)],"collision":[("same",False),("same",False)],
             "archive_fallback":[("archive",True),("fallback",False)]}[state["documents"]]
    docs = []
    for index,(title,archived) in enumerate(specs):
        source = directory/f"source-{index}"
        archive = directory/f"archive-{index}"
        source.write_bytes((f"source {index} {title}\x00").encode())
        archive.write_bytes((f"archive {index} {title}\xff").encode())
        doc = types.SimpleNamespace(title=title,source_path=source,archive_path=archive,
                                    filename=f"folder/document-{index}.txt",archive_filename=f"folder/document-{index}.pdf",
                                    has_archive_version=archived)
        def public_filename(self, *, archive=False, counter=0):
            return self.title+(f"_{counter:02}" if counter else "")+(".pdf" if archive else ".txt")
        doc.get_public_filename = types.MethodType(public_filename,doc)
        docs.append(doc)
    sink = io.BytesIO(); error = None
    try:
        compression = zipfile.ZIP_STORED if state["compression"]=="stored" else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(sink,"w",compression=compression) as archive:
            strategy = env[state["strategy"]](archive,follow_formatting=case["follow_formatting"])
            for doc in docs:
                strategy.add_document(doc)
    except Exception as exc:
        error = dict(type=type(exc).__name__,message=str(exc))
    with zipfile.ZipFile(sink) as archive:
        entries = [dict(name=entry.filename,content_hex=archive.read(entry).hex()) for entry in archive.infolist()]
    return dict(error=error,return_value=None,entries=entries)


def saleor_scenarios(case):
    flags = [(False,False)] if case["contract"]=="disabled" else list(itertools.product([False,True],repeat=2))
    return [dict(quantity_update=qu,quantity=q,replace=replace,deleted=deleted,prequeued=queued,
                 price_update=price,reason_update=reason,metadata=bool(index%2))
            for index,(qu,q,replace,deleted,queued,(price,reason)) in enumerate(itertools.product(
                [False,True],[-1,0,3],[False,True],[False,True],[False,True],flags))]


def saleor_support(dto_source, metadata_source):
    dto = named(ast.parse(dto_source).body,ast.ClassDef,"CheckoutLineData")
    model = named(ast.parse(metadata_source).body,ast.ClassDef,"ModelWithMetadata")
    metadata = named(model.body,ast.FunctionDef,"store_value_in_metadata")
    # Exact upstream dataclass/method bodies; only dependency imports are supplied.
    imports = ast.parse("from __future__ import annotations\nfrom dataclasses import dataclass, field\nfrom decimal import Decimal").body
    tree = ast.Module(body=imports+[dto,metadata],type_ignores=[])
    return tree


def saleor_observation(tree, support, case, state):
    if case["contract"]=="disabled" and (state["price_update"] or state["reason_update"]):
        raise ValueError("State violates disabled price capability contract")
    env = execute_tree(support,"source_saleor_types")
    function = execute_tree(tree,"source_saleor_function")["_append_line_to_update"]
    Line = type("BoundaryLine",(),{"store_value_in_metadata":env["store_value_in_metadata"]})
    line = Line(); line.pk=41; line.quantity=5
    line.price_override=decimal.Decimal("10.00"); line.price_override_reason="previous"
    line.metadata={"existing":"kept"}
    data = env["CheckoutLineData"](quantity=state["quantity"],quantity_to_update=state["quantity_update"],
        custom_price=decimal.Decimal("7.50"),custom_price_to_update=state["price_update"],
        custom_price_reason="manual",custom_price_reason_to_update=state["reason_update"],
        metadata_list=[types.SimpleNamespace(key="new",value="value")] if state["metadata"] else [])
    to_update = [line] if state["prequeued"] else []
    to_delete = [line] if state["deleted"] else []
    error = None; result = None
    try:
        result = function(to_update,to_delete,data,state["replace"],line)
    except Exception as exc:
        error = dict(type=type(exc).__name__,message=str(exc))
    return dict(error=error,return_value=result,quantity=line.quantity,
                price=str(line.price_override),reason=line.price_override_reason,metadata=line.metadata,
                to_update=[item.pk for item in to_update],to_delete=[item.pk for item in to_delete])
