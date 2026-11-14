export default function PageTitle({ children }: { children: string }) {
  const parts = children.trim().split(/\s+/).filter(Boolean);
  const [first, ...rest] = parts;
  if (!first) return null;
  return (
    <>
      {first}
      {rest.length > 0 && (
        <>
          {" "}
          <span className="page-title-accent">{rest.join(" ")}</span>
        </>
      )}
    </>
  );
}
