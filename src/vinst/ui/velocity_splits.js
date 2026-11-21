// The layers' bar is the slider: one knob on each boundary, dragged to where the next velocity starts.
// A drag moves only the bar here; letting go sends the new starts, as a slider's change does.
const TOP = 127;

export default {
  template: `
    <div class="vi-splits" :class="{ 'vi-splits-dragging': dragging !== null }">
      <div class="vi-splits-track">
        <div v-for="(seg, k) in segments" :key="k" class="vi-splits-seg"
             :style="{ left: seg.left, width: seg.width, background: colours[k % colours.length] }">
          {{ labels[k] }} {{ seg.lo }}–{{ seg.hi }}
        </div>
      </div>
      <div v-for="(start, i) in starts" :key="i" class="vi-splits-knob" role="slider" tabindex="0"
           :aria-label="labels[i + 1] + ' starts at velocity'" :aria-valuenow="start"
           :aria-valuemin="lowest(i)" :aria-valuemax="highest(i)" :style="{ left: at(start) }"
           @pointerdown="grab(i, $event)" @keydown="key(i, $event)">
        <span v-if="dragging === i" class="vi-splits-value">{{ start }}</span>
      </div>
    </div>
  `,
  props: {
    modelValue: Array,
    labels: Array,
    colours: Array,
  },
  emits: ["update:modelValue"],
  data() {
    return { starts: [...(this.modelValue || [])], dragging: null };
  },
  computed: {
    segments() {
      const edges = [1, ...this.starts, TOP + 1];
      return edges.slice(0, -1).map((lo, k) => {
        const hi = edges[k + 1] - 1;
        return { lo, hi, left: this.at(lo), width: ((hi - lo + 1) / TOP) * 100 + "%" };
      });
    },
  },
  watch: {
    modelValue(value) {
      if (this.dragging === null) this.starts = [...(value || [])];
    },
  },
  methods: {
    at(velocity) {
      return ((velocity - 1) / TOP) * 100 + "%";
    },
    // Each layer keeps at least one velocity.
    lowest(i) {
      return i === 0 ? 2 : this.starts[i - 1] + 1;
    },
    highest(i) {
      return i === this.starts.length - 1 ? TOP : this.starts[i + 1] - 1;
    },
    put(i, velocity) {
      this.starts[i] = Math.min(this.highest(i), Math.max(this.lowest(i), Math.round(velocity)));
    },
    commit() {
      const changed = this.starts.some((v, i) => v !== (this.modelValue || [])[i]);
      if (changed) this.$emit("update:modelValue", [...this.starts]);
    },
    grab(i, event) {
      if (event.button !== 0) return;
      event.preventDefault();
      const knob = event.currentTarget;
      knob.focus();
      knob.setPointerCapture(event.pointerId);
      this.dragging = i;
      const track = this.$el.getBoundingClientRect();
      const move = (e) => this.put(i, ((e.clientX - track.left) / track.width) * TOP + 1);
      const release = () => {
        knob.removeEventListener("pointermove", move);
        knob.removeEventListener("pointerup", release);
        knob.removeEventListener("pointercancel", release);
        this.dragging = null;
        this.commit();
      };
      knob.addEventListener("pointermove", move);
      knob.addEventListener("pointerup", release);
      knob.addEventListener("pointercancel", release);
    },
    key(i, event) {
      const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -10, PageUp: 10 }[event.key];
      if (step === undefined) return;
      event.preventDefault();
      this.put(i, this.starts[i] + (event.shiftKey ? step * 10 : step));
      this.commit();
    },
  },
};
