import collections


def continuousSubarray(list):
    queue = collections.deque()
    queue.append(list[0])
    l,r = len(list),0
    ans = 0
    for i in range(len(list)):
        top = queue[len(queue)-1]
        if list[i]<top:
            queue.pop()
            l = min(l,list.index(top))
            r = max(r,list.index(top)+1)
            print("l:",l,"r:",r)
        else:
            queue.append(list[i])

        if r<l:
            ans = len(list)-l
        else:
            ans = r-l+1

    return ans

list = [1,2,3,4]

print(continuousSubarray(list))