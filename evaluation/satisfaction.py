from __future__ import annotations
import math
def wilson_lower_bound(successes:int,total:int,z:float=1.96)->float:
    if total<=0:return 0.0
    p=successes/total; z2=z*z; denominator=1+z2/total; centre=p+z2/(2*total); margin=z*math.sqrt((p*(1-p)+z2/(4*total))/total); return (centre-margin)/denominator
