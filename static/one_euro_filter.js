/**
 * Author: Gery Casiez
 * Details: https://gery.casiez.net/1euro/
 *
 * Copyright 2019 Inria
 * 
 * BSD License https://opensource.org/licenses/BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 *  1. Redistributions of source code must retain the above copyright notice, this list of conditions
 * and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions
 * and the following disclaimer in the documentation and/or other materials provided with the distribution.
 * 
 * 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or
 * promote products derived from this software without specific prior written permission.

 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WA
RRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PUR
POSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, IN
CIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GO
ODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, W
HETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 */

class LowPassFilter {
    
  setAlpha(alpha) {
    if (alpha<=0.0 || alpha>1.0) 
      console.log("alpha should be in (0.0., 1.0]");
    this.a = alpha;
  }

  constructor(alpha, initval=0.0) {
    this.y = this.s = initval;
    this.setAlpha(alpha);
    this.initialized = false;
  }

  filter(value) {
    var result;
    if (this.initialized)
      result = this.a*value + (1.0-this.a) * this.s;
    else {
      result = value;
      this.initialized = true;
    }
    this.y = value;
    this.s = result;
    return result;
  }

  filterWithAlpha(value, alpha) {
    this.setAlpha(alpha);
    return this.filter(value);
  }

  hasLastRawValue() {
    return this.initialized;
  }

  lastRawValue() {
    return this.y;
  }

  lastFilteredValue() {
    return this.s;
  }

  reset() {
    this.initialized = false;
  }

}

// -----------------------------------------------------------------

class OneEuroFilter {

  alpha(cutoff) {
    var te = 1.0 / this.freq;
    var tau = 1.0 / (2 * Math.PI * cutoff);
    return 1.0 / (1.0 + tau/te);
  }

  setFrequency(f) {
    if (f<=0) console.log("freq should be >0") ;
    this.freq = f;
  }

  setMinCutoff(mc) {
    if (mc<=0) console.log("mincutoff should be >0");
    this.mincutoff = mc;
  }

  setBeta(b) {
    this.beta_ = b;
  }

  setDerivateCutoff(dc) {
    if (dc<=0) console.log("dcutoff should be >0") ;
    this.dcutoff = dc ;
  }

  constructor(freq, mincutoff=1.0, beta_=0.0, dcutoff=1.0) {
    this.setFrequency(freq) ;
    this.setMinCutoff(mincutoff) ;
    this.setBeta(beta_) ;
    this.setDerivateCutoff(dcutoff) ;
    this.x = new LowPassFilter(this.alpha(mincutoff)) ;
    this.dx = new LowPassFilter(this.alpha(dcutoff)) ;
    this.lasttime = undefined ;
  }

  reset() {
    this.x.reset();
    this.dx.reset();
    this.lasttime = undefined;
  }

  filter(value, timestamp=undefined) {
    // update the sampling frequency based on timestamps
    if (this.lasttime!=undefined && timestamp!=undefined && timestamp > this.lasttime)
      this.freq = 1.0 / (timestamp-this.lasttime) ;
    this.lasttime = timestamp ;
    // estimate the current variation per second 
    var dvalue = this.x.hasLastRawValue() ? (value - this.x.lastFilteredValue())*this.freq : 0.0 ; 
    var edvalue = this.dx.filterWithAlpha(dvalue, this.alpha(this.dcutoff)) ;
    // use it to update the cutoff frequency
    var cutoff = this.mincutoff + this.beta_ * Math.abs(edvalue) ;
    // filter the given value
    return this.x.filterWithAlpha(value, this.alpha(cutoff)) ;
  }
} 