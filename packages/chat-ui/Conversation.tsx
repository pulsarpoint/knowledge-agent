import { memo, useId, useState, type ReactNode } from 'react';
import { ArrowUp, Check, Copy, Square } from 'lucide-react';
import Markdown from '~/components/Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '~/components/ui/empty';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupTextarea } from '~/components/ui/input-group';
import { Spinner } from '~/components/ui/spinner';
import { cn } from '~/lib/utils';
import { Bubble, BubbleContent } from './ui/bubble';
import { Message, MessageContent, MessageFooter, MessageHeader } from './ui/message';
import { MessageScroller, MessageScrollerButton, MessageScrollerContent, MessageScrollerItem, MessageScrollerProvider, MessageScrollerViewport } from './ui/message-scroller';

export interface ConversationMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  name?: string;
  pending?: boolean;
  status?: string;
  error?: string;
  details?: ReactNode;
  actions?: ReactNode;
}

export interface ConversationComposer {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop?: () => void;
  disabled?: boolean;
  blockedReason?: string | null;
  pending?: boolean;
  label?: string;
  sendLabel?: string;
  stopLabel?: string;
  placeholder?: string;
  maxLength?: number;
}

const Answer = memo(function Answer({ content, pending }: { content: string; pending?: boolean }) {
  return <Markdown content={content} isStreaming={pending} />;
});

/** Conversation presentation only: callers own requests, sessions and response actions. */
export default function Conversation({ messages, composer, emptyTitle = 'What would you like to work on?', emptyDescription = 'Send a message to start a conversation.', className }: {
  messages: ConversationMessage[];
  composer?: ConversationComposer;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
}) {
  const id = useId();
  const [copied, setCopied] = useState<string>();
  const [copyError, setCopyError] = useState('');
  const canSend = composer && !composer.disabled && !composer.pending && !composer.blockedReason && Boolean(composer.value.trim());
  return <section aria-label="Conversation" className={cn('flex min-h-0 min-w-0 flex-col', className)}>
    <MessageScrollerProvider autoScroll>
      <MessageScroller className="flex-1">
        <MessageScrollerViewport aria-label="Messages" role="log">
          <MessageScrollerContent className="mx-auto max-w-3xl gap-8 px-4 py-6 sm:px-6">
            {messages.length === 0 && <MessageScrollerItem messageId="empty" className="my-auto">
              <Empty><EmptyHeader><EmptyTitle>{emptyTitle}</EmptyTitle><EmptyDescription>{emptyDescription}</EmptyDescription></EmptyHeader></Empty>
            </MessageScrollerItem>}
            {messages.map(message => <MessageScrollerItem key={message.id} messageId={message.id} scrollAnchor={message.role === 'user'}>
              <Message align={message.role === 'user' ? 'end' : 'start'} aria-label={message.role === 'user' ? 'Your message' : 'Agent response'}>
                <MessageContent>
                  <MessageHeader>{message.name ?? (message.role === 'user' ? 'You' : 'Assistant')}</MessageHeader>
                  {message.details}
                  {message.error && <Alert variant="destructive"><AlertDescription>{message.error}</AlertDescription></Alert>}
                  {message.content && <Bubble variant={message.role === 'user' ? 'muted' : 'ghost'} align={message.role === 'user' ? 'end' : 'start'}>
                    <BubbleContent>{message.role === 'user' ? <p className="whitespace-pre-wrap break-words">{message.content}</p> : <Answer content={message.content} pending={message.pending} />}</BubbleContent>
                  </Bubble>}
                  {message.pending && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner />{message.status || 'Thinking…'}</p>}
                  {!message.pending && message.status && <p className="text-xs text-muted-foreground">{message.status}</p>}
                  {message.role === 'assistant' && !message.pending && <MessageFooter className="flex-wrap gap-1">
                    {message.content && <Button type="button" variant="ghost" size="sm" aria-label="Copy response" onClick={async () => {
                      try { await navigator.clipboard.writeText(message.content); setCopied(message.id); setCopyError(''); }
                      catch { setCopyError('Could not copy the response. Select and copy the text instead.'); }
                    }}>{copied === message.id ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}{copied === message.id ? 'Copied' : 'Copy'}</Button>}
                    {message.actions}
                  </MessageFooter>}
                </MessageContent>
              </Message>
            </MessageScrollerItem>)}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
    {copyError && <Alert variant="destructive"><AlertDescription>{copyError}</AlertDescription></Alert>}
    {composer && <form className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-3 pt-3 sm:px-6" onSubmit={event => { event.preventDefault(); if (canSend) composer.onSend(); }}>
      <FieldGroup><Field>
        <FieldLabel className="sr-only" htmlFor={id}>{composer.label ?? 'Message'}</FieldLabel>
        <InputGroup>
          <InputGroupTextarea id={id} rows={2} className="max-h-40 min-h-16" value={composer.value}
            maxLength={composer.maxLength ?? 20000} disabled={composer.disabled}
            aria-describedby={`${id}-help`} placeholder={composer.placeholder ?? 'Message the agent…'}
            onChange={event => composer.onChange(event.target.value)} onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                event.preventDefault(); if (canSend) composer.onSend();
              }
            }} />
          <InputGroupAddon align="block-end" className="justify-between">
            <span className="text-xs">Enter to send · Shift + Enter for a new line</span>
            {composer.pending && composer.onStop ? <InputGroupButton size="icon-sm" variant="outline" aria-label={composer.stopLabel ?? 'Stop response'} onClick={composer.onStop}><Square data-icon="inline-start" /></InputGroupButton>
              : <InputGroupButton size="icon-sm" type="submit" variant="default" disabled={!canSend} aria-label={composer.sendLabel ?? 'Send message'}>{composer.pending ? <Spinner /> : <ArrowUp data-icon="inline-start" />}</InputGroupButton>}
          </InputGroupAddon>
        </InputGroup>
        {composer.blockedReason && <FieldDescription id={`${id}-help`}>{composer.blockedReason}</FieldDescription>}
      </Field></FieldGroup>
    </form>}
  </section>;
}
