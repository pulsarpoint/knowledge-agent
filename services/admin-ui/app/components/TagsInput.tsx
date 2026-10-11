import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { MAX_TAGS, TAG_PATTERN } from "~/lib/documents";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { FieldDescription, FieldError } from "~/components/ui/field";
import { Input } from "~/components/ui/input";

const BAD_TAG = "Tags use lowercase letters, digits and -, up to 40 characters.";

/** Tags typed one at a time: Enter or comma adds the typed tag (trimmed, lower-cased); × removes one. */
export default function TagsInput({ id, value, onChange, suggestions }: { id: string; value: string[]; onChange: (tags: string[]) => void; suggestions: string[] }) {
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const full = value.length >= MAX_TAGS;
  function add() {
    const tag = text.trim().toLowerCase();
    if (!tag) return;
    if (!TAG_PATTERN.test(tag)) { setProblem(BAD_TAG); return; }
    setProblem(null); setText("");
    if (!value.includes(tag)) onChange([...value, tag]);
  }
  function key(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") { event.preventDefault(); add(); }
  }
  return <div className="flex flex-col gap-2">
    {value.length > 0 && <ul className="flex flex-wrap gap-1">{value.map(tag => <li key={tag}><Badge variant="secondary" className="gap-1">{tag}
      <Button type="button" variant="ghost" size="icon" className="size-4" aria-label={`Remove tag ${tag}`} onClick={() => onChange(value.filter(item => item !== tag))}><X /></Button></Badge></li>)}</ul>}
    <Input id={id} role="combobox" aria-label="Tags" list={`${id}-suggestions`} value={text} disabled={full} placeholder="Type a tag and press Enter"
      aria-invalid={problem !== null} onChange={event => { setText(event.target.value); setProblem(null); }} onKeyDown={key} onBlur={add} />
    <datalist id={`${id}-suggestions`}>{suggestions.filter(tag => !value.includes(tag)).map(tag => <option key={tag} value={tag} />)}</datalist>
    {problem ? <FieldError>{problem}</FieldError> : full ? <FieldDescription>Use at most {MAX_TAGS} tags.</FieldDescription>
      : <FieldDescription>Lowercase letters, digits and -. Features find prompts by tag, for example server-discovery.</FieldDescription>}
  </div>;
}
