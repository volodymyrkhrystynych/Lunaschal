import { TOOL_SIZES, type StrokeTool } from '@/lib/paper';
import { InkToolPanel } from '@/components/ink/InkToolPanel';
import type { Size } from '@/lib/ink';

interface PaperToolPanelProps {
  tool: StrokeTool;
  onToolChange: (tool: StrokeTool) => void;
  /** Index into TOOL_SIZES[tool] — each tool remembers its own width. */
  sizeIndex: number;
  onSizeIndexChange: (index: number) => void;
  /** The active tool's colour — each tool remembers its own, the same way it
   * remembers its own width. */
  color?: string;
  onColorChange?: (color: string) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  /** The drawing area the panel floats over, in CSS pixels. */
  bounds: Size;
}

/** Paper's tool panel: the shared InkToolPanel with an A4 page's widths.
 *
 * Thin on purpose. The panel itself — its drag, its edge snapping, its
 * fixed-size controls and the reasons for them — lives in
 * src/components/ink/InkToolPanel.tsx and is shared with the newspaper reader,
 * so a fix to any of that reaches both. All Paper supplies is what is specific
 * to writing on an A4 sheet: the widths, in page units, and the fact that there
 * is nothing underneath to scroll, so no Read tool is offered.
 */
export function PaperToolPanel({
  tool,
  onToolChange,
  ...rest
}: PaperToolPanelProps) {
  return (
    <InkToolPanel
      tool={tool}
      sizes={TOOL_SIZES[tool]}
      // Paper offers no Read tool, so the panel can only ever hand back a
      // stroke tool here.
      onToolChange={next => onToolChange(next as StrokeTool)}
      {...rest}
    />
  );
}
