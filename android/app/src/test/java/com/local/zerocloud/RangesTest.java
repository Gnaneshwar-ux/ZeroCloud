package com.local.zerocloud;
import org.junit.Test;
import static org.junit.Assert.*;
public class RangesTest {
 @Test public void bounded(){assertArrayEquals(new long[]{0,499},Ranges.parse("bytes=0-499",1000));}
 @Test public void openEnd(){assertArrayEquals(new long[]{500,999},Ranges.parse("bytes=500-",1000));}
 @Test public void suffix(){assertArrayEquals(new long[]{900,999},Ranges.parse("bytes=-100",1000));}
 @Test public void clamp(){assertArrayEquals(new long[]{0,999},Ranges.parse("bytes=0-9999",1000));}
 @Test(expected=IllegalArgumentException.class) public void outside(){Ranges.parse("bytes=1000-",1000);}
 @Test(expected=IllegalArgumentException.class) public void multiple(){Ranges.parse("bytes=0-1,2-3",1000);}
 @Test(expected=IllegalArgumentException.class) public void reversed(){Ranges.parse("bytes=20-10",1000);}
 @Test(expected=IllegalArgumentException.class) public void invalid(){Ranges.parse("bytes=nope-",1000);}
 @Test(expected=IllegalArgumentException.class) public void emptyFile(){Ranges.parse("bytes=0-",0);}
}
