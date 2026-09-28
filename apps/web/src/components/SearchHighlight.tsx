export type SearchContext = {
  keywords: string[];
  previews?: Record<string, { text: string; source: string; matched_fields: string[] }>;
};

export default function SearchHighlight({ text, terms }: { text: string; terms: string[] }) {
  const words = [...new Set(terms.filter(Boolean))].sort((a, b) => b.length - a.length);
  if (!words.length) return <>{text}</>;
  const pattern = words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const lower = words.map((word) => word.toLocaleLowerCase());
  // Render text nodes, never HTML supplied by the index, document or search input.
  return <>{text.split(new RegExp(`(${pattern})`, "gi")).map((part, index) =>
    lower.includes(part.toLocaleLowerCase()) ? <mark key={index}>{part}</mark> : part
  )}</>;
}
