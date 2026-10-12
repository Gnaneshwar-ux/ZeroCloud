package com.local.zerocloud;
/** Single byte ranges only. Invalid requests never seek outside the catalog file. */
public final class Ranges {
    public static long[] parse(String value,long size){
        if(size<=0||value==null||!value.startsWith("bytes=")||value.contains(","))throw new IllegalArgumentException();
        String[] parts=value.substring(6).split("-",-1);if(parts.length!=2)throw new IllegalArgumentException();
        try{long start,end;if(parts[0].isEmpty()){long suffix=Long.parseLong(parts[1]);if(suffix<=0)throw new IllegalArgumentException();start=Math.max(0,size-suffix);end=size-1;}else{start=Long.parseLong(parts[0]);end=parts[1].isEmpty()?size-1:Math.min(size-1,Long.parseLong(parts[1]));}if(start<0||start>=size||end<start)throw new IllegalArgumentException();return new long[]{start,end};}catch(NumberFormatException e){throw new IllegalArgumentException(e);}
    }
}
