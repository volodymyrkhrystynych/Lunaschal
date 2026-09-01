let modulePromise: Promise<unknown> | null = null;

export async function renderMusicXml(xml: string): Promise<string[]> {
  const [{ default: createVerovioModule }, { VerovioToolkit }] =
    await Promise.all([import('verovio/wasm'), import('verovio/esm')]);
  modulePromise ??= createVerovioModule();
  const module = await modulePromise;
  // The ESM wrapper's generated declaration requires the concrete Emscripten
  // module type, while the factory export intentionally exposes it as an
  // implementation detail. Runtime construction is the package's documented
  // integration path.
  // @ts-expect-error Verovio does not export its Emscripten module type.
  const toolkit = new VerovioToolkit(module);
  toolkit.setOptions({
    pageWidth: 1800,
    pageHeight: 2400,
    scale: 45,
    adjustPageHeight: true,
    footer: 'none',
    header: 'none',
  });
  if (!toolkit.loadData(xml))
    throw new Error('Verovio could not read this score.');
  return Array.from({ length: toolkit.getPageCount() }, (_, index) =>
    toolkit.renderToSVG(index + 1)
  );
}
