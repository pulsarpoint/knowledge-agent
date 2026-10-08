import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { beforeEach, expect, it, vi } from 'vitest';
import AgentTestView from '../AgentTestView';
import * as api from '../../lib/operations';
import { getAgentConfig, getRunResourceOptions, type RunResourceOptions } from '../../lib/profiles';
vi.mock('../../lib/operations');
vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgentConfig: vi.fn(), getRunResourceOptions: vi.fn() }));
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const run: api.OperationsRun = { id:'run-1',session_id:'session-1',task:'Initial question',status:'completed',created_at:'2026-09-11T12:00:00Z',actor_email:'admin@test',answer:'Initial answer',error:'',progress:'',commands:[],context:{servers:[],model_name:'Model',prompt:{version:1},config:{id:'custom'}} };
const conversation: api.AgentConversation = { id:'session-1',is_test:true,title:'Initial question',status:'active',created_at:run.created_at,last_activity_at:run.created_at,expires_at:'2026-09-11T13:00:00Z',config:{id:'custom'},runs:[run],reviews:[] };
function Page() { const location = useLocation(); return <><output aria-label="URL">{location.search}</output><AgentTestView agentId="custom" agentName="Custom agent" available ready dirty={false} active /></>; }
function renderPage(query='?view=test') { return render(<MemoryRouter initialEntries={[query]}><Page /></MemoryRouter>); }
beforeEach(()=>{
  vi.resetAllMocks();
  vi.mocked(api.getOperationsRuns).mockResolvedValue({runs:[]});
  vi.mocked(api.getAgentSessions).mockResolvedValue({sessions:[]});
  vi.mocked(api.getAgentSession).mockResolvedValue(structuredClone(conversation));
  vi.mocked(getAgentConfig).mockResolvedValue({id:'custom'});
  vi.mocked(getRunResourceOptions).mockResolvedValue({ resources: 'fixed' });
});

it('creates a session URL on the first message and sends follow-ups to the same session',async()=>{
  const saved = structuredClone(conversation); saved.runs=[];
  vi.mocked(api.getAgentSession).mockImplementation(async()=>structuredClone(saved));
  vi.mocked(api.startOperationsRun).mockImplementation(async(task,agentId,signal,onEvent,sessionId)=>{
    expect(agentId).toBe('custom'); expect(signal.aborted).toBe(false);
    expect(sessionId).toBe(saved.runs.length ? 'session-1' : undefined);
    const result = {...run,id:`run-${saved.runs.length+1}`,task,answer:`Answer ${saved.runs.length+1}`};
    onEvent({event:'started',data:{...result,status:'running',answer:''}});
    saved.runs.push(result);
    onEvent({event:'completed',data:result});
  });
  renderPage();
  fireEvent.change(screen.getByLabelText('Task'),{target:{value:'First message'}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Run task'})).toBeEnabled());  // after the run resource options load
  fireEvent.click(screen.getByRole('button',{name:'Run task'}));
  expect(await screen.findByText('Answer 1')).toBeVisible();
  expect(screen.getByLabelText('URL')).toHaveTextContent('session_id=session-1');
  expect(screen.getByLabelText('Task')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('Task'),{target:{value:'Follow up'}});
  fireEvent.click(screen.getByRole('button',{name:'Send message'}));
  expect(await screen.findByText('Answer 2')).toBeVisible();
  expect(screen.getByText('Answer 1')).toBeVisible();
  expect(api.startOperationsRun).toHaveBeenCalledTimes(2);
});

it('restores the full conversation by URL without sending another message',async()=>{
  vi.mocked(api.getAgentSession).mockResolvedValue({...conversation,runs:[run,{...run,id:'run-2',task:'Second question',answer:'Second answer'}]});
  renderPage('?view=test&session_id=session-1');
  expect(await screen.findByText('Initial answer')).toBeVisible();
  expect(screen.getByText('Second answer')).toBeVisible();
  expect(api.startOperationsRun).not.toHaveBeenCalled();
  expect(api.getAgentSession).toHaveBeenCalledWith('custom','session-1');
});

it('closes explicitly with confirmation and starts new conversations without the old session ID',async()=>{
  vi.mocked(api.closeAgentSession).mockImplementation(async()=>{vi.mocked(api.getAgentSession).mockResolvedValue({...conversation,status:'closed'});});
  renderPage('?view=test&session_id=session-1');
  await screen.findByText('Initial answer');
  fireEvent.click(screen.getByRole('button',{name:'Close session'}));
  expect(api.closeAgentSession).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button',{name:'Close session'}));
  expect(await screen.findByText('This session is closed or expired. Start a new session to continue.')).toBeVisible();
  expect(api.closeAgentSession).toHaveBeenCalledWith('custom','session-1');
  fireEvent.click(screen.getByRole('button',{name:'New session'}));
  await waitFor(()=>expect(screen.getByLabelText('URL')).not.toHaveTextContent('session_id'));
  expect(screen.queryByText('Initial answer')).not.toBeInTheDocument();
});

it('shows reviewable tuning suggestions and preserves feedback after a failed review',async()=>{
  vi.mocked(api.reviewAgentSession).mockRejectedValueOnce(new Error('Review unavailable')).mockResolvedValueOnce({id:'review-1',comment:'Be concise',suggestion:'Suggested instructions: report evidence first.',created_at:run.created_at});
  renderPage('?view=test&session_id=session-1');
  await screen.findByText('Initial answer');
  expect(screen.queryByLabelText('Your feedback')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Add feedback'}));
  fireEvent.change(screen.getByLabelText('Your feedback'),{target:{value:'Be concise'}});
  fireEvent.click(screen.getByRole('button',{name:'Suggest improvements'}));
  expect(await screen.findByText('Review unavailable')).toBeVisible();
  expect(screen.getByLabelText('Your feedback')).toHaveValue('Be concise');
  fireEvent.click(screen.getByRole('button',{name:'Suggest improvements'}));
  expect(await screen.findByText('Suggested instructions: report evidence first.')).toBeVisible();
  expect(api.reviewAgentSession).toHaveBeenLastCalledWith('custom','session-1','Be concise','run-1');
  expect(screen.getByRole('link',{name:'Edit system prompt'})).toHaveAttribute('href','/?view=prompts&session_id=session-1');
  expect(screen.getByLabelText('Your feedback')).toHaveValue('');
});

it('reconnects to a running session and updates progress without retrying execution',async()=>{
  vi.mocked(api.getAgentSession).mockResolvedValueOnce({...conversation,runs:[{...run,status:'running',answer:'',progress:'Inspecting files'}]}).mockResolvedValue(conversation);
  renderPage('?view=test&session_id=session-1');
  await screen.findByText('Inspecting files');
  vi.useFakeTimers();
  try { await act(async()=>{await vi.advanceTimersByTimeAsync(2000);}); }
  finally { vi.useRealTimers(); }
  expect(await screen.findByText('Initial answer')).toBeVisible();
  expect(api.startOperationsRun).not.toHaveBeenCalled();
});

it('previews the pinned session configuration instead of newly edited settings',async()=>{
  vi.mocked(api.getAgentSession).mockResolvedValue({...conversation, config:{id:'custom',instructions:'Pinned instructions',config_version:{id:'v1',version:1,label:'Baseline'}}});
  renderPage('?view=test&session_id=session-1');
  expect(await screen.findByText('Agent v1 · Baseline')).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Show config'}));
  expect(await screen.findByLabelText('Agent configuration')).toHaveTextContent('Pinned instructions');
  expect(getAgentConfig).not.toHaveBeenCalled();
});

it('sends the selected version for a new test',async()=>{
  vi.mocked(getAgentConfig).mockResolvedValue({id:'custom',config_version:{id:'v2',version:2,label:'Experiment'}});
  renderPage('?view=test&version_id=v2');
  expect(await screen.findByText('Agent v2 · Experiment')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Task'),{target:{value:'Try the draft'}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Run task'})).toBeEnabled());
  expect(getRunResourceOptions).toHaveBeenCalledWith('custom','v2');
  fireEvent.click(screen.getByRole('button',{name:'Run task'}));
  await waitFor(()=>expect(api.startOperationsRun).toHaveBeenCalledWith('Try the draft','custom',expect.any(AbortSignal),expect.any(Function),undefined,'v2',undefined));
});

it('totals recorded session usage across turns without double-counting cache or reasoning',async()=>{
  vi.mocked(api.getAgentSession).mockResolvedValue({...conversation,runs:[
    {...run,context:{...run.context,usage:{requests:2,input_tokens:1200,output_tokens:300,total_tokens:1500,
      input_tokens_details:{cached_tokens:800,cache_write_tokens:100},output_tokens_details:{reasoning_tokens:120}}}},
    {...run,id:'run-2',context:{...run.context,usage:{requests:1,input_tokens:600,output_tokens:200,total_tokens:800}}},
  ]});
  renderPage('?view=test&session_id=session-1');
  const stats = await screen.findByRole('region',{name:'Session usage'});
  expect(within(stats).getByText('2,300')).toBeVisible();
  expect(within(stats).getByText('1,800')).toBeVisible();
  expect(within(stats).getByText('500')).toBeVisible();
  expect(within(stats).getByText('3')).toBeVisible();
  expect(stats).toHaveTextContent('800 cached input tokens · 100 cache-write tokens · 120 reasoning tokens');
  expect(stats).not.toHaveTextContent('partial');
});

it('marks missing and old zero usage as unavailable, and mixed totals as partial',async()=>{
  const saved = {...conversation,runs:[{...run,context:{...run.context,usage:{requests:0,input_tokens:0,output_tokens:0,total_tokens:0}}}]};
  vi.mocked(api.getAgentSession).mockResolvedValue(saved);
  renderPage('?view=test&session_id=session-1');
  const stats = await screen.findByRole('region',{name:'Session usage'});
  expect(within(stats).getAllByText('Unavailable')).toHaveLength(4);
  expect(stats).toHaveTextContent('Usage was not recorded for 1 turn.');
  vi.mocked(api.startOperationsRun).mockImplementation(async(task,agentId,signal,onEvent)=>{
    const next = {...run,id:'run-2',task,context:{...run.context,usage:{requests:1,input_tokens:80,output_tokens:20,total_tokens:100}}};
    onEvent({event:'started',data:{...next,status:'running',answer:'',context:run.context}});
    saved.runs.push(next);
    onEvent({event:'completed',data:next});
    onEvent({event:'completed',data:next});
  });
  fireEvent.change(screen.getByLabelText('Task'),{target:{value:'Follow up'}});
  fireEvent.click(screen.getByRole('button',{name:'Send message'}));
  await waitFor(()=>expect(stats).toHaveTextContent('Totals are partial.'));
  expect(within(stats).getByText('100')).toBeVisible();
  expect(within(stats).getByText('80')).toBeVisible();
  expect(within(stats).getByText('20')).toBeVisible();
  expect(within(stats).getByText('1')).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'New session'}));
  await waitFor(()=>expect(screen.queryByRole('region',{name:'Session usage'})).not.toBeInTheDocument());
});

it('keeps completed usage visible while a new reply is pending',async()=>{
  vi.mocked(api.getAgentSession).mockResolvedValue({...conversation,runs:[
    {...run,context:{...run.context,usage:{requests:1,input_tokens:80,output_tokens:20,total_tokens:100}}},
    {...run,id:'run-2',status:'running',answer:'',progress:'Working'},
  ]});
  renderPage('?view=test&session_id=session-1');
  const stats = await screen.findByRole('region',{name:'Session usage'});
  expect(within(stats).getByText('100')).toBeVisible();
  expect(stats).toHaveTextContent('Current reply’s usage is pending.');
  expect(stats).not.toHaveTextContent('not recorded');
});


it('links every reply to its full run record',async()=>{
  renderPage('?view=test&session_id=session-1');
  await screen.findByText('Initial answer');
  expect(screen.getByRole('link',{name:'Full record'})).toHaveAttribute('href','/activity/runs/run%3Arun-1');
});

const ANALYST: RunResourceOptions = { resources: 'per_run', collections: null, servers: [], services: null,
  code_projects: [{ id: 'repo', name: 'Payments API' }, { id: 'worker', name: 'Payments worker' }] };
const ONLY_REPO = { code_project_ids: ['repo'], collection_ids: [], server_ids: [], service_ids: [] };

it('a per-run agent picks the resources for the run and shows them in the configuration', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  renderPage();
  const picker = await screen.findByRole('region', { name: 'Resources for this run' });
  expect(within(picker).queryByText('Collections')).toBeNull();  // a kind the agent does not use
  expect(within(picker).getByText('None allowed. Choose them in the agent’s tabs.')).toBeVisible();  // servers
  fireEvent.click(within(picker).getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  const shown = await screen.findByLabelText('Agent configuration');
  expect(JSON.parse(shown.textContent!).run_resources).toEqual(ONLY_REPO);
});

it('the chosen resources start the conversation once and stay with it', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  const saved = structuredClone(conversation); saved.runs = [];
  saved.config = { id: 'custom', code: { projects: [{ id: 'repo', name: 'Payments API' }] }, run_resources: ONLY_REPO };
  vi.mocked(api.getAgentSession).mockImplementation(async () => structuredClone(saved));
  vi.mocked(api.startOperationsRun).mockImplementation(async (task, _agentId, _signal, onEvent) => {
    const result = { ...run, id: `run-${saved.runs.length + 1}`, task, answer: `Answer ${saved.runs.length + 1}`,
      context: { ...run.context, config: saved.config } };
    onEvent({ event: 'started', data: { ...result, status: 'running', answer: '' } });
    saved.runs.push(result);
    onEvent({ event: 'completed', data: result });
  });
  renderPage();
  fireEvent.click(within(await screen.findByRole('region', { name: 'Resources for this run' })).getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Where are the limits?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
  expect(await screen.findByText('Answer 1')).toBeVisible();
  expect(vi.mocked(api.startOperationsRun).mock.calls[0][6]).toEqual(ONLY_REPO);
  expect(screen.queryByRole('region', { name: 'Resources for this run' })).toBeNull();  // fixed for the conversation
  expect(screen.getByLabelText('Resources for this run')).toHaveTextContent('Resources for this run: Payments API');
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'And the callers?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(await screen.findByText('Answer 2')).toBeVisible();
  expect(vi.mocked(api.startOperationsRun).mock.calls[1][6]).toBeUndefined();  // follow-ups never pass resources
});

it('a per-run agent waits for its resource options before the first message can be sent', async () => {
  let resolve!: (value: RunResourceOptions) => void;
  vi.mocked(getRunResourceOptions).mockReturnValue(new Promise(done => { resolve = done; }));
  renderPage();
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Too early' } });
  expect(screen.getByText('Loading the resources for this run…')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
  await act(async () => resolve(structuredClone(ANALYST)));
  expect(await screen.findByRole('region', { name: 'Resources for this run' })).toBeVisible();
  expect(screen.queryByText('Loading the resources for this run…')).toBeNull();
});

it('a reopened conversation shows the resources it started with, whatever the agent uses now', async () => {
  vi.mocked(api.getAgentSession).mockResolvedValue({ ...structuredClone(conversation),
    config: { id: 'custom', code: { projects: [{ id: 'repo', name: 'Payments API' }] }, run_resources: ONLY_REPO } });
  renderPage('?view=test&session_id=session-1');  // the agent is fixed now
  expect(await screen.findByLabelText('Resources for this run')).toHaveTextContent('Resources for this run: Payments API');
});

it('a fixed conversation shows no run resources, even if the agent is per-run now', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  renderPage('?view=test&session_id=session-1');
  await screen.findByText('Initial answer');
  await waitFor(() => expect(getRunResourceOptions).toHaveBeenCalled());
  expect(screen.queryByLabelText('Resources for this run')).toBeNull();
});

it('a choice the reloaded options no longer offer is not sent, and the options stay visible while reloading', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  const view = (revision: number) => <MemoryRouter initialEntries={['?view=test']}><AgentTestView agentId="custom" agentName="Custom agent" available ready dirty={false} active revision={revision} /></MemoryRouter>;
  const { rerender } = render(view(1));
  const picker = await screen.findByRole('region', { name: 'Resources for this run' });
  fireEvent.click(within(picker).getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.click(within(picker).getByRole('checkbox', { name: 'Payments worker' }));
  let resolve!: (value: RunResourceOptions) => void;
  vi.mocked(getRunResourceOptions).mockReturnValue(new Promise(done => { resolve = done; }));
  rerender(view(2));
  expect(screen.getByRole('region', { name: 'Resources for this run' })).toBeVisible();
  await act(async () => resolve({ ...structuredClone(ANALYST), code_projects: [{ id: 'repo', name: 'Payments API' }] }));
  await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Payments worker' })).toBeNull());
  vi.mocked(api.startOperationsRun).mockResolvedValue(undefined);
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Where are the limits?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
  await waitFor(() => expect(api.startOperationsRun).toHaveBeenCalled());
  expect(vi.mocked(api.startOperationsRun).mock.calls[0][6]).toEqual(ONLY_REPO);
});

it('says when nothing is chosen for the run', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  renderPage();
  const picker = await screen.findByRole('region', { name: 'Resources for this run' });
  const note = 'Nothing chosen: the agent’s tools will have nothing to work on';
  expect(within(picker).getByText(note)).toBeVisible();
  fireEvent.click(within(picker).getByRole('checkbox', { name: 'Payments API' }));
  expect(within(picker).queryByText(note)).toBeNull();
});

it('blocks the first message until the run resource options load, with a retry', async () => {
  vi.mocked(getRunResourceOptions).mockRejectedValueOnce(new Error('Options unavailable')).mockResolvedValue(structuredClone(ANALYST));
  renderPage();
  fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Too early' } });
  expect(await screen.findByRole('button', { name: 'Retry' })).toBeVisible();
  expect(screen.getAllByText('Options unavailable').length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('region', { name: 'Resources for this run' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Run task' })).toBeEnabled();
});

it('keeps the message box with the picker, in a shorter conversation area', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  renderPage();
  await screen.findByRole('region', { name: 'Resources for this run' });
  expect(screen.getByLabelText('Task')).toBeVisible();
  expect(screen.getByRole('region', { name: 'Conversation' }).className).toContain('h-[calc(100dvh-30rem)]');
});

it('a Test chat answer shows its tools and Show config lists the conversation\'s', async () => {
  vi.mocked(api.getAgentSession).mockResolvedValue({ ...conversation, tool_counts: [{ name: 'read_file', calls: 2 }],
    runs: [{ ...run, tool_counts: [{ name: 'read_file', calls: 2 }] }, { ...run, id: 'run-2', task: 'Second', answer: 'Second answer', tool_counts: [] }] });
  renderPage('?view=test&session_id=session-1');
  await screen.findByText('Second answer');
  expect(screen.getAllByLabelText('Tools used').map(line => line.textContent)).toEqual(['read_file 2', 'No tools used']);
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  await waitFor(() => expect(screen.getByLabelText('Agent configuration')).toHaveTextContent('"tool_counts"'));
});
