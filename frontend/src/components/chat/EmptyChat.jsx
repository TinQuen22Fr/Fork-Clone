/* En-tete licence identique aux autres composants chat/ */

export default function EmptyChat() {
  const suggestions = [
    "Write me a function in Rust that...",
    "Explain quantum entanglement in 3 lines",
    "Roast my CV (paste below)",
    "Generate a startup name and pitch",
  ];
  return (
    <div className="py-12">
      <div className="text-center mb-10">
        <div className="text-xs uppercase tracking-[0.3em] text-[#ffd700] font-bold mb-2">
          // session initialized
        </div>
        <h2 className="font-heading text-3xl md:text-4xl font-black tracking-tighter">
          WHAT DO WE <span className="text-[#ff2a6d]">FORGE</span> TODAY?
        </h2>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-2xl mx-auto">
        {suggestions.map((s, i) => (
          <div
            key={i}
            className="border-2 border-white/15 p-4 text-sm text-gray-300 hover:border-[#ffd700] hover:text-white hover:shadow-[4px_4px_0_0_#05d9e8] transition-all cursor-default"
            data-testid={`suggestion-${i}`}
          >
            {s}
          </div>
        ))}
      </div>
    </div>
  );
}
