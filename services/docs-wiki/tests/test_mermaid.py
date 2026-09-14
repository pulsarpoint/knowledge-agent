import asyncio

from docs_wiki.mermaid import MermaidValidator


def test_real_validator_keeps_valid_diagrams_and_preserves_invalid_source_as_text():
    async def run():
        validator = MermaidValidator()
        chart = 'flowchart LR\n A["API (v1)"] --> B["Database"]'
        assert (await validator.validate(chart))['valid']
        answer = f'Explanation [1].\n\n```mermaid\n{chart}\n```\n\n```mermaid\nflowchart LR\nA[unclosed\n```\n\nStill useful.'
        checked = await validator.validate_answer(answer)
        assert f'```mermaid\n{chart}\n```' in checked
        assert '```text\nflowchart LR\nA[unclosed\n```' in checked
        assert checked.startswith('Explanation [1].') and checked.endswith('Still useful.')
        assert len(validator.checked) == 2
    asyncio.run(run())


def test_validator_failure_does_not_fail_prose_or_execute_diagram_source(monkeypatch):
    monkeypatch.setenv('MERMAID_VALIDATOR_SCRIPT', '/missing/validator.mjs')
    async def run():
        validator = MermaidValidator()
        assert await validator.validate_answer('Plain answer [1].') == 'Plain answer [1].'
        source = 'Helpful answer.\n~~~mermaid\nflowchart LR\nA-->B\n~~~'
        assert await validator.validate_answer(source) == source.replace('~~~mermaid', '~~~text')
    asyncio.run(run())


def test_markdown_examples_are_not_treated_as_actual_diagrams():
    answer = '````markdown\n```mermaid\ninvalid example\n```\n````'
    validator = MermaidValidator()
    assert asyncio.run(validator.validate_answer(answer)) == answer
    assert validator.checked == {}
