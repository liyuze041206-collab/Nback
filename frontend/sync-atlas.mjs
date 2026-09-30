import {copyFile,cp,mkdir} from 'node:fs/promises';
const target=new URL('./public/',import.meta.url);
await mkdir(target,{recursive:true});
await copyFile(new URL('../gspm_network_atlas.html',import.meta.url),new URL('architecture.html',target));
await copyFile(new URL('../gspm_portal.html',import.meta.url),new URL('portal.html',target));
await copyFile(new URL('../gspm_shared_theme.css',import.meta.url),new URL('gspm_shared_theme.css',target));

await copyFile(new URL('../gspm_ui.js',import.meta.url),new URL('gspm_ui.js',target));
await cp(new URL('../assets/',import.meta.url),new URL('assets/',target),{recursive:true});
