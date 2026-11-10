"""Adapter for existing source-bound descriptions, independent of project names."""
from .api import Verdict


class SourceDescriptionValidator:
    def validate(self, context, candidate, store):
        # The default adapters already put the existing scripts directory on
        # sys.path. This adapter reuses its original evidence/admission review.
        from . import adapters  # noqa: F401
        from region_contracts import inspect_description_evidence
        gates=candidate.get('gates',[])
        if not gates or any(g.get('kind')!='source_description' for g in gates):
            return Verdict('unknown','Missing source-bound description')
        results=[]
        for gate in gates:
            result=inspect_description_evidence(store.document(gate['input']),store.root)
            if 'expected' in gate and result!=store.document(gate['expected']):
                raise ValueError('Existing description review changed')
            results.append(result)
        allowed=all((r['decision'].get('selection') or {}).get('database_regions') for r in results)
        return Verdict('allow' if allowed else 'unknown',
                       'Existing source-description admission; observations alone do not certify a backend',
                       {'reviews':results})
