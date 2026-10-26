import { useServicePage } from "./ServiceLayout";
import EntryDocuments from "./EntryDocuments";

/** The service's documents. */
export default function ServiceDocuments() {
  const { service, version, changed } = useServicePage();
  return (
    <EntryDocuments
      entry={{ kind: "service", id: service.id, name: service.name }}
      refresh={version}
      onChanged={changed}
    />
  );
}
