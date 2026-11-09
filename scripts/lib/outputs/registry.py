"""Explicit report output registry, no dynamic imports or implicit cloud writes.

Only local private file output is enabled by default. Future Feishu Bitable,
Feishu Docs or other adapters MUST register explicitly, implement ReportOutput,
and receive public_projection(document) without raw SSE or utterance/excerpt.
"""
from lib.report_document import public_projection
from lib.outputs.base import OutputContext
from lib.outputs.file import LocalFileOutput

_REGISTRY = {"file": (LocalFileOutput, "private")}


def register_output(kind, factory, *, privacy="shared"):
    """Allow vetted extensions, but never auto-load a filename as executable."""
    if (not isinstance(kind,str) or not kind.isidentifier() or
            kind in _REGISTRY or privacy != "shared" or not callable(factory)):
        raise ValueError("invalid_report_output_registration")
    _REGISTRY[kind] = (factory, privacy)


def validate_report_outputs(cfg):
    raw = cfg.get("report_outputs", [{"type":"file","enabled":True}])
    if not isinstance(raw,list) or not raw:
        raise ValueError("report_outputs_must_be_nonempty_list")
    enabled = []
    seen=set()
    for item in raw:
        if not isinstance(item,dict):
            raise ValueError("report_output_entry_invalid")
        kind=item.get("type")
        if not isinstance(kind,str) or kind not in _REGISTRY:
            # Do not echo arbitrary user-supplied sink strings or URLs.
            raise ValueError("report_output_not_supported")
        if not isinstance(item.get("enabled",True),bool):
            raise ValueError("report_output_enabled_must_be_boolean")
        if kind in seen:
            raise ValueError("report_output_duplicate")
        seen.add(kind)
        if item.get("enabled",True):
            enabled.append(kind)
    if not enabled:
        raise ValueError("report_outputs_all_disabled")
    return enabled


def dispatch_report_outputs(document, cfg, root):
    kinds=validate_report_outputs(cfg)
    context=OutputContext(root=root,reports=cfg.get("reports",{}))
    receipts=[]
    for kind in kinds:
        factory,privacy=_REGISTRY[kind]
        sink=factory()
        payload=document if privacy=="private" else public_projection(document)
        receipt=sink.publish(payload,context)
        if (not isinstance(receipt,dict) or receipt.get("status")!="saved" or
                receipt.get("type")!=kind):
            raise ValueError("report_output_invalid_receipt")
        receipts.append(receipt)
    return receipts