import { Plus } from "lucide-react";

export default function EmptyState({ onStart }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-20">
      <img src="/icon-192.png" alt="" className="w-24 h-24 mb-6 border-2 border-white/20" />
      <h2 className="font-heading text-3xl md:text-5xl font-black tracking-tighter mb-4">
        WELCOME TO <span className="text-[#ffd700]">THE FORGE</span>
      </h2>
      <p className="text-gray-400 max-w-md mb-8">
        Start a new conversation to unleash Claude.
      </p>
      <button
        onClick={onStart}
        className="btn-primary flex items-center gap-2"
        data-testid="empty-state-new-chat-btn"
      >
        <Plus className="w-4 h-4" /> Start Chat
      </button>
    </div>
  );
}
