import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { feature } from 'topojson-client'
import type { Topology, GeometryCollection } from 'topojson-specification'
import countries from 'world-atlas/countries-110m.json'
import { desktop } from '../lib/desktop'
import 'leaflet/dist/leaflet.css'
export type PlaceGroup = { id: string; latitude: number; longitude: number; mediaIds: string[] }
type Location = { mediaId:string;latitude:number;longitude:number }
export function Places({ onFilter, onError, active = true, exploring = false, placeId, onReset, onGroups }: { onFilter:(place:PlaceGroup)=>void;onError:(message:string)=>void;active?:boolean;exploring?:boolean;placeId?:string;onReset?:()=>void;onGroups?:(places:PlaceGroup[])=>void }) {
  const container = useRef<HTMLDivElement>(null), map = useRef<L.Map | null>(null), layer = useRef<L.LayerGroup | null>(null)
  const mapObserver = useRef<ResizeObserver | null>(null)
  const [locations,setLocations] = useState<Location[]>([])
  const [search, setSearch] = useState('')
  const onChoose = useRef(onFilter)
  useEffect(() => { onChoose.current = onFilter },[onFilter])
  const groups = useMemo(() => {
    const result = new Map<string,PlaceGroup>()
    for (const value of locations) {
      const id = `${value.latitude.toFixed(2)},${value.longitude.toFixed(2)}`
      const group = result.get(id) ?? {id,latitude:value.latitude,longitude:value.longitude,mediaIds:[]}
      group.mediaIds.push(value.mediaId); result.set(id,group)
    }
    return [...result.values()].sort((a,b) => b.mediaIds.length-a.mediaIds.length)
  },[locations])
  useEffect(() => {
    if (!desktop || !active) return
    let mounted = true; let timer:ReturnType<typeof setTimeout>
    const poll = async () => { try { const values = await desktop!.request<Location[]>('locations'); if (mounted) setLocations((previous) => JSON.stringify(previous) === JSON.stringify(values) ? previous : values) } catch(error) { if(mounted)onError(String(error)) } if(mounted)timer=setTimeout(()=>void poll(),4000) }
    void poll();return()=>{mounted=false;clearTimeout(timer)}
  },[onError,active])
  useEffect(() => { if (active) onGroups?.(groups) }, [groups,active,onGroups])
  useEffect(() => {
    if (!container.current || !active) return
    if (map.current) { map.current.invalidateSize(); return }
    const instance = L.map(container.current,{minZoom:1,maxZoom:16,zoomSnap:.25,worldCopyJump:true}).setView([20,0],2)
    map.current = instance
    const topology = countries as unknown as Topology<{countries:GeometryCollection}>
    L.geoJSON(feature(topology,topology.objects.countries),{style:{color:'#b4c9d6',weight:1,fillColor:'#edf3e8',fillOpacity:1},onEachFeature:(country, polygon) => { if(country.properties?.name) { const bounds=(polygon as L.Polygon).getBounds();const large=bounds.getNorth()-bounds.getSouth()>10;polygon.bindTooltip(String(country.properties.name),{permanent:large,direction:'center',className:large?'country-label':''}) } }}).addTo(instance)
    instance.attributionControl.setPrefix(false); instance.attributionControl.addAttribution('Offline geography: Natural Earth · Map: Leaflet')
    L.control.scale().addTo(instance); layer.current = L.layerGroup().addTo(instance)
    mapObserver.current = new ResizeObserver(()=>instance.invalidateSize());mapObserver.current.observe(container.current)
  },[active])
  useEffect(() => () => { mapObserver.current?.disconnect();map.current?.remove();map.current=null;layer.current=null },[])
  const initiallyFitted = useRef(false)
  useEffect(() => {
    if (!map.current || !layer.current || !active) return
    layer.current.clearLayers()
    for (const group of groups) {
      L.circleMarker([group.latitude,group.longitude],{radius:Math.min(18,7+Math.log2(group.mediaIds.length+1))+(placeId===group.id?3:0),color:placeId===group.id?'#184a85':'#fff',weight:placeId===group.id?3:2,fillColor:placeId===group.id?'#77aaf0':'#286dc4',fillOpacity:.9}).bindTooltip(`${group.mediaIds.length} photos · ${group.id}`).on('click',()=>onChoose.current(group)).addTo(layer.current)
    }
    if(groups.length && !initiallyFitted.current) { map.current.fitBounds(L.latLngBounds(groups.map((group)=>[group.latitude,group.longitude])),{padding:[40,40],maxZoom:4});initiallyFitted.current=true }
  },[groups,placeId,active])
  useEffect(() => { if (active && placeId) { const selected = groups.find((group)=>group.id===placeId); if(selected)map.current?.panTo([selected.latitude,selected.longitude],{animate:false}) } },[placeId,active,groups])
  const visibleGroups = groups.filter((group)=>!search.trim() || `${group.id} ${group.latitude.toFixed(4)} ${group.longitude.toFixed(4)}`.includes(search.trim()))
  return <section className={`places-section ${exploring ? 'compact-places explorer-panel' : ''}`} aria-label={exploring ? 'Places explorer' : 'Photo places'}><div className="section-heading"><div><h2>Places</h2><p>{locations.length.toLocaleString()} GPS-tagged photos · {groups.length.toLocaleString()} nearby location groups</p></div></div><p className="folder-help">Local EXIF indexing runs alongside face indexing. Click a map marker or location to see its photos. This offline map includes countries and coastlines, not street tiles; no coordinates are sent to a map service.</p>{!desktop && <p>Use the desktop app to index GPS metadata from linked folders.</p>}{exploring && <button className="explorer-reset" onClick={()=>{setSearch('');onReset?.()}}>All locations</button>}<div className="places-layout"><div ref={container} className="offline-map" aria-label="Offline photo locations map" /><aside className="place-list"><label className="explorer-search"><input aria-label="Find a location" placeholder="Find coordinates" value={search} onChange={(event)=>setSearch(event.target.value)} /></label>{groups.length ? visibleGroups.map((group)=><button key={group.id} aria-pressed={placeId===group.id} className={placeId===group.id?'current-place':''} onClick={()=>onFilter(group)}><strong>{group.mediaIds.length} {group.mediaIds.length === 1 ? 'photo' : 'photos'}</strong><span>{group.latitude.toFixed(4)}°, {group.longitude.toFixed(4)}°</span></button>) : <div className="empty-state"><strong>No GPS locations indexed yet</strong><p>Photos without GPS are not assigned a guessed location. New markers appear as metadata is read.</p></div>}{!!groups.length && !visibleGroups.length && <p className="explorer-no-results">No matching locations.</p>}</aside></div></section>
}
