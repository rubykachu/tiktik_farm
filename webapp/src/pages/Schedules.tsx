import { useState } from "react";
import { Plus } from "lucide-react";
import PipelinesTable from "../components/PipelinesTable";
import PipelineFormModal from "../components/PipelineFormModal";
import { Pipeline } from "../api/pipelines";

export default function SchedulesPage() {
  const [formOpen, setFormOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Pipeline | null>(null);

  function openCreate() {
    setEditTarget(null);
    setFormOpen(true);
  }

  function openEdit(p: Pipeline) {
    setEditTarget(p);
    setFormOpen(true);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Lịch đăng</h2>
          <p className="text-sm text-slate-500 mt-1">Quản lý lịch đăng video tự động theo kênh.</p>
        </div>
        <button
          className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium bg-brand-600 hover:bg-brand-700 text-white"
          onClick={openCreate}
        >
          <Plus size={16} /> Tạo lịch mới
        </button>
      </div>

      <PipelinesTable onEdit={openEdit} />

      <PipelineFormModal
        open={formOpen}
        pipeline={editTarget}
        onClose={() => setFormOpen(false)}
      />
    </div>
  );
}
