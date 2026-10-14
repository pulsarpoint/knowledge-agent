import type { DocumentInput, DocumentResources } from './api';
import { DOCUMENT_KINDS } from '~/lib/resources';
import ResourcePicker, { deploymentCascade } from '~/components/ResourcePicker';

export default function DocumentResourceSelection({ resources, value, onChange }: {
  resources: DocumentResources; value: DocumentInput; onChange: (value: DocumentInput) => void;
}) {
  return <ResourcePicker kinds={DOCUMENT_KINDS} options={resources} value={value} onChange={onChange} idPrefix="doc" layout="cards" showUnavailable
    description={({ key, plural }) => key === 'deployments' ? 'Select a deployment to distinguish environments. Its service and server are selected too.'
      : `Link the ${plural.toLowerCase()} this document describes.`}
    emptyText={({ plural }) => `No ${plural.toLowerCase()} available.`}
    cascade={deploymentCascade<DocumentInput>(resources.deployments)} />;
}
