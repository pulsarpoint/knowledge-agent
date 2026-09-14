import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, expect, it, vi } from 'vitest';
import AgentVersionsView from '../AgentVersionsView';
import * as api from '../../lib/agentVersions';
vi.mock('../../lib/agentVersions');
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));
const active: api.AgentVersion = { id:'v1',version:1,label:'Baseline',notes:'',status:'active',created_at:'2026-09-11T12:00:00Z',published_at:'2026-09-11T12:00:00Z',source_review_id:null };
const draft: api.AgentVersion = { ...active,id:'v2',version:2,label:'Clear evidence',status:'draft',published_at:null };
const source: api.DraftSource = { config:{ system_prompt:{content:'Use evidence.'},instructions:'Original instructions',model:{id:'model'},ssh:{enabled:true,servers:[{id:'server-1',name:'Test server',instructions:'Inspect only.'}]} }, source_hash:'a'.repeat(64), suggestion:'Be clearer.', changes:{system_prompt:null,instructions:'State uncertainty.',server_instructions:[]} };
const changed = vi.fn();
function page(query='?view=versions', dirty=false) { return render(<MemoryRouter initialEntries={[query]}><AgentVersionsView agentId="custom" dirty={dirty} onChanged={changed} /></MemoryRouter>); }
beforeEach(()=>{
  vi.resetAllMocks();
  vi.mocked(api.getAgentVersions).mockResolvedValue({active_id:'v1',versions:[draft,active]});
  vi.mocked(api.getVersionSource).mockResolvedValue(structuredClone(source));
  vi.mocked(api.createAgentVersion).mockResolvedValue({id:'v3',version:3,label:'Updated'});
  vi.mocked(api.activateAgentVersion).mockResolvedValue({id:'v2'});
});
it('opens independent version test links and activates only after confirmation',async()=>{
  page();
  const test = await screen.findByRole('link',{name:'Test v2'});
  expect(test).toHaveAttribute('href','/?view=test&version_id=v2');
  expect(test).toHaveAttribute('target','_blank');
  fireEvent.click(screen.getByRole('button',{name:'Activate v2'}));
  expect(api.activateAgentVersion).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button',{name:'Activate version'}));
  await waitFor(()=>expect(api.activateAgentVersion).toHaveBeenCalledWith('custom','v2','v1'));
  expect(await screen.findByRole('status')).toHaveTextContent('Version 2 is now active.');
});
it('prefills editable review changes, keeps resource snapshot, and saves without activation',async()=>{
  page('?view=versions&review_id=review-1');
  expect(await screen.findByLabelText('Additional instructions')).toHaveValue('State uncertainty.');
  expect(api.getVersionSource).toHaveBeenCalledWith('custom',{review_id:'review-1'});
  expect(screen.getByLabelText('System prompt')).toHaveValue('Use evidence.');
  expect(screen.getByLabelText('Test server instructions')).toHaveValue('Inspect only.');
  fireEvent.change(screen.getByLabelText('Additional instructions'),{target:{value:'Distinguish facts from guesses.'}});
  fireEvent.change(screen.getByLabelText('Version label'),{target:{value:'Clearer summary'}});
  fireEvent.click(screen.getByRole('button',{name:'Save draft version'}));
  await waitFor(()=>expect(api.createAgentVersion).toHaveBeenCalledWith('custom',expect.objectContaining({
    source_review_id:'review-1',source_hash:source.source_hash,label:'Clearer summary',
    changes:{system_prompt:'Use evidence.',instructions:'Distinguish facts from guesses.',server_instructions:[]},
  })));
  expect(api.activateAgentVersion).not.toHaveBeenCalled();
  expect(await screen.findByRole('status')).toHaveTextContent('saved as a draft');
});
it('keeps draft edits when saving fails and blocks snapshots of unsaved settings',async()=>{
  vi.mocked(api.createAgentVersion).mockRejectedValue(new Error('Source configuration changed.'));
  const view = page();
  fireEvent.click(screen.getByRole('button',{name:'Create draft from settings'}));
  fireEvent.change(await screen.findByLabelText('Additional instructions'),{target:{value:'Keep my edit.'}});
  fireEvent.click(screen.getByRole('button',{name:'Save draft version'}));
  expect(await screen.findByText('Source configuration changed.')).toBeVisible();
  expect(screen.getByLabelText('Additional instructions')).toHaveValue('Keep my edit.');
  view.unmount(); page('?view=versions',true);
  expect(screen.getByRole('button',{name:'Create draft from settings'})).toBeDisabled();
});
