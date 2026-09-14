import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import Conversation from '@knowledge-agent/chat-ui/conversation';
vi.mock('../Markdown', () => ({ default: ({content}: {content:string}) => <div>{content}</div> }));

it('sends with Enter, preserves Shift+Enter and composition, and blocks duplicate sends while working', () => {
  const onSend = vi.fn(); const onStop = vi.fn();
  const composer = { value:'A question', onChange:vi.fn(), onSend, onStop };
  const view = render(<Conversation messages={[]} composer={composer} />);
  const input = screen.getByRole('textbox', {name:'Message'});
  fireEvent.keyDown(input, {key:'Enter',shiftKey:true});
  fireEvent.keyDown(input, {key:'Enter',isComposing:true,keyCode:229});
  expect(onSend).not.toHaveBeenCalled();
  fireEvent.keyDown(input, {key:'Enter'});
  expect(onSend).toHaveBeenCalledTimes(1);
  view.rerender(<Conversation messages={[]} composer={{...composer,pending:true}} />);
  fireEvent.keyDown(input, {key:'Enter'});
  expect(onSend).toHaveBeenCalledTimes(1);
  expect(input).toBeEnabled();
  fireEvent.click(screen.getByRole('button', {name:'Stop response'}));
  expect(onStop).toHaveBeenCalledOnce();
});

it('renders caller response actions without adding tuning UI or requiring a composer', () => {
  render(<Conversation messages={[
    {id:'user',role:'user',content:'Question'},
    {id:'answer',role:'assistant',name:'Coordinator',content:'Answer',actions:<button>Review answer</button>},
  ]} />);
  expect(screen.getByText('Answer')).toBeVisible();
  expect(screen.getByRole('button',{name:'Review answer'})).toBeVisible();
  expect(screen.getByRole('button',{name:'Copy response'})).toBeVisible();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.queryByText('Tune agent')).not.toBeInTheDocument();
});
