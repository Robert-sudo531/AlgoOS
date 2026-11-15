// Reference solution for USACO March 2014 Gold, The Lazy Cow.
// Transform Manhattan diamonds into axis-aligned inclusive rectangles.
#include <algorithm>
#include <iostream>
#include <vector>

struct Event {
    int x, lo, hi, delta;
};

struct MaxTree {
    std::vector<int> peak, lazy;
    explicit MaxTree(int n) : peak(4 * n), lazy(4 * n) {}
    void add(int node, int lo, int hi, int a, int b, int delta) {
        if (a <= lo && hi <= b) {
            peak[node] += delta;
            lazy[node] += delta;
            return;
        }
        const int mid = (lo + hi) / 2;
        if (a <= mid) add(2 * node, lo, mid, a, b, delta);
        if (b > mid) add(2 * node + 1, mid + 1, hi, a, b, delta);
        peak[node] = lazy[node] + std::max(peak[2 * node], peak[2 * node + 1]);
    }
};

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n, k;
    if (!(std::cin >> n >> k)) return 1;
    std::vector<Event> events;
    std::vector<int> ys;
    events.reserve(2 * n);
    ys.reserve(2 * n);
    for (int i = 0; i < n; ++i) {
        int grass, x, y;
        std::cin >> grass >> x >> y;
        const int u = x + y, v = x - y;
        events.push_back({u - k, v - k, v + k, grass});
        events.push_back({u + k + 1, v - k, v + k, -grass});
        ys.push_back(v - k);
        ys.push_back(v + k);
    }
    std::sort(ys.begin(), ys.end());
    ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
    std::sort(events.begin(), events.end(), [](const auto& a, const auto& b) { return a.x < b.x; });
    MaxTree tree(static_cast<int>(ys.size()));
    int answer = 0;
    for (std::size_t i = 0; i < events.size();) {
        const int x = events[i].x;
        do {
            const auto& e = events[i++];
            const int a = std::lower_bound(ys.begin(), ys.end(), e.lo) - ys.begin();
            const int b = std::lower_bound(ys.begin(), ys.end(), e.hi) - ys.begin();
            tree.add(1, 0, static_cast<int>(ys.size()) - 1, a, b, e.delta);
        } while (i < events.size() && events[i].x == x);
        answer = std::max(answer, tree.peak[1]);
    }
    std::cout << answer << '\n';
}
