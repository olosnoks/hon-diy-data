/**
 * Save a text file on the device (GPX, KML, GeoJSON, CSV). In the browser
 * this is a download of a Blob made here: nothing is uploaded anywhere.
 * TODO(integrator): route through the platform file store (#12) when it has
 * a save dialog for Tauri.
 */
export type SaveFn = (filename: string, mime: string, text: string) => void;

export const saveText: SaveFn = (filename, mime, text) => {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.replace(/[\\/:*?"<>|]+/g, '_');
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
