import { useState } from 'react';
import brands from '../brands.json';
export function BrandLogo({ carrier }: { carrier: string }) {
 const displayCarrier = carrier.replace(/\s*·\s*온라인 전용/g, '').trim();
 const name = displayCarrier.split(' · ')[0].trim();
 const key = name === 'LGU+' ? 'LG U+' : name;
 const logo = (brands as Record<string, {src:string}>)[key];
 const [failed, setFailed] = useState(false);
 return <span className="brand-identity">{logo && !failed ? <img className="brand-logo" src={logo.src} alt={name + ' 로고'} onError={() => setFailed(true)} /> : <span className="brand-fallback">{name}</span>}<span className="carrier-label">{displayCarrier}</span></span>;
}
