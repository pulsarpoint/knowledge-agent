"""Native SDK tracing exports to OTel with task-local redaction, including errors."""
import asyncio
import json

from agents import set_trace_processors, trace
from agents.tracing import function_span, generation_span
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from knowledge_admin.sessions.tracing import instrument, trace_secrets


def test_native_traces_redact_inputs_outputs_errors_and_concurrent_credentials():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrument(provider)

    async def run(name):
        secret = name + '-private-key'
        with trace_secrets([secret]), trace(name):
            with function_span(name=secret, input=json.dumps({'credential':secret})) as tool:
                await asyncio.sleep(0)
                tool.span_data.output = 'Evidence ' + secret
                tool.set_error({'message':'provider failed '+secret, 'data':{'key':secret}})
            with generation_span(model='fixture', input=[{'role':'user','content':secret}],
                    output=[{'role':'assistant','content':'Finding '+secret}]):
                await asyncio.sleep(0)

    async def both():
        await asyncio.gather(run('first'), run('second'))

    try:
        asyncio.run(both())
        recorded = exporter.get_finished_spans()
        serialized = json.dumps([{'name':s.name,'attributes':dict(s.attributes), 'error':s.status.description} for s in recorded])
        assert '[redacted]' in serialized and 'Evidence' in serialized and 'Finding' in serialized
        assert 'first-private-key' not in serialized and 'second-private-key' not in serialized
        assert 'provider failed' not in serialized
        assert len({s.context.trace_id for s in recorded}) == 2
        assert len(recorded) == 6
    finally:
        set_trace_processors([])
        provider.shutdown()
